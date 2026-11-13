// SPDX-License-Identifier: GPL-2.0
/* Deterministic IPv4 peer on a guest-only TUN interface. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/if_tun.h>
#include <linux/ip.h>
#include <linux/reboot.h>
#include <linux/tcp.h>
#include <net/if.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>

#include <unistd.h>

static int tun;
static uint16_t local_port;
static uint32_t base_seq;
static const uint32_t peer_seq = 50001;

static void check(int ok, const char *what)
{
	if (!ok) {
		fprintf(stderr, "RTX_PACKET FAIL %s errno=%d\n", what, errno);
		fflush(NULL);
#ifndef RTX_NATIVE_PEER
		sync();
		reboot(LINUX_REBOOT_CMD_POWER_OFF);
#endif
		_exit(1);
	}
}

static uint16_t checksum(const void *data, size_t len, uint32_t sum)
{
	const unsigned char *p = data;
	while (len > 1) {
		sum += ((uint16_t)p[0] << 8) | p[1];
		p += 2;
		len -= 2;
	}
	if (len)
		sum += (uint16_t)*p << 8;
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return htons((uint16_t)~sum);
}

static void reply(uint32_t ack, int syn, int fin, int sack)
{
	unsigned char packet[80] = {0};
	struct iphdr *ip = (void *)packet;
	struct tcphdr *tcp = (void *)(packet + sizeof(*ip));
	unsigned char *opt = (void *)(tcp + 1);
	unsigned int olen = syn ? 8 : sack ? 12 : 0;
	unsigned int tlen = sizeof(*tcp) + olen;
	uint32_t pseudo;

	ip->version = 4;
	ip->ihl = 5;
	ip->tot_len = htons(sizeof(*ip) + tlen);
	ip->ttl = 64;
	ip->protocol = IPPROTO_TCP;
	inet_pton(AF_INET, "198.18.0.2", &ip->saddr);
	inet_pton(AF_INET, "198.18.0.1", &ip->daddr);
	ip->check = checksum(ip, sizeof(*ip), 0);
	tcp->source = htons(8080);
	tcp->dest = local_port;
	tcp->seq = htonl(syn ? peer_seq - 1 : peer_seq);
	tcp->ack_seq = htonl(ack);
	tcp->doff = tlen / 4;
	tcp->syn = syn;
	tcp->fin = fin;
	tcp->ack = 1;
	tcp->window = htons(32000);
	if (syn) {
		unsigned char options[] = {2, 4, 3, 232, 4, 2, 1, 1};
		memcpy(opt, options, sizeof(options));
	} else if (sack) {
		uint32_t left = htonl(base_seq + (sack == 2 ? 0 : 1000));
		uint32_t right = htonl(base_seq + (sack == 2 ? 1000 : 2000));
		opt[0] = 1; opt[1] = 1; opt[2] = 5; opt[3] = 10;
		memcpy(opt + 4, &left, 4);
		memcpy(opt + 8, &right, 4);
	}
	pseudo = (ntohl(ip->saddr) >> 16) + (ntohl(ip->saddr) & 0xffff) +
		 (ntohl(ip->daddr) >> 16) + (ntohl(ip->daddr) & 0xffff) +
		 IPPROTO_TCP + tlen;
	tcp->check = checksum(tcp, tlen, pseudo);
	check(write(tun, packet, sizeof(*ip) + tlen) == (ssize_t)(sizeof(*ip) + tlen), "peer write");
}

static struct tcphdr *receive(unsigned char *packet, int *payload)
{
	struct pollfd pfd = { .fd = tun, .events = POLLIN };
	struct iphdr *ip;
	struct tcphdr *tcp;
	ssize_t n;

	check(poll(&pfd, 1, 5000) == 1, "peer poll");
	n = read(tun, packet, 65536);
	check(n >= 40, "short packet");
	ip = (void *)packet;
	check(ip->version == 4 && ip->protocol == IPPROTO_TCP, "IPv4 TCP");
	check(ip->ihl >= 5 && n >= ip->ihl * 4 + 20, "IPv4 header length");
	tcp = (void *)(packet + ip->ihl * 4);
	check(tcp->doff >= 5 && n >= ip->ihl * 4 + tcp->doff * 4, "TCP header length");
	*payload = ntohs(ip->tot_len) - ip->ihl * 4 - tcp->doff * 4;
	check(*payload >= 0 && n >= ntohs(ip->tot_len), "packet length");
	check(!tcp->rst, "unexpected reset");
	return tcp;
}

static void write_file(const char *path, const char *value)
{
	int fd = open(path, O_WRONLY);
	check(fd >= 0, path);
	check(write(fd, value, strlen(value)) == (ssize_t)strlen(value), path);
	close(fd);
}

static void packet_case(int scenario)
{
	int sack = scenario == 1 || scenario == 5;
	const char *name = scenario == 3 ? "PARTIAL" : scenario == 4 ? "DSACK" :
		scenario == 5 ? "RENEGING" : sack ? "SACK" : scenario == 0 ? "LOSS" : "RTO";
	char marker[32];
	struct sockaddr_in addr = { .sin_family = AF_INET, .sin_port = htons(8080) };
	unsigned char packet[65536], data[4000];
	unsigned char seen[4000] = {0};
	struct tcphdr *tcp;
	int sock, payload, received = 0, duplicate = 0;
	uint32_t offset;

	snprintf(marker, sizeof(marker), "CASE %s\n", name);
#ifdef RTX_NATIVE_PEER
	printf("RTX_NATIVE %s", marker);
#else
	write_file("/sys/kernel/debug/tracing/trace_marker", marker);
#endif
	write_file("/proc/sys/net/ipv4/tcp_early_retrans", scenario == 2 ? "0\n" : "3\n");
	sock = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
	check(sock >= 0, "socket");
	inet_pton(AF_INET, "198.18.0.2", &addr.sin_addr);
	check(connect(sock, (void *)&addr, sizeof(addr)) == -1 && errno == EINPROGRESS, "connect");
	tcp = receive(packet, &payload);
	check(tcp->syn, "SYN");
	local_port = tcp->source;
	base_seq = ntohl(tcp->seq) + 1;
	reply(base_seq, 1, 0, 0);
	tcp = receive(packet, &payload);
	check(tcp->ack && !payload, "handshake ACK");
	for (size_t i = 0; i < sizeof(data); i++)
		data[i] = (unsigned char)(i * 31 + 7);
	check(send(sock, data, sizeof(data), MSG_NOSIGNAL) == sizeof(data), "send");
	while (received < 4000) {
		tcp = receive(packet, &payload);
		offset = ntohl(tcp->seq) - base_seq;
		check(payload > 0 && offset + payload <= sizeof(data), "data range");
		check(!memcmp((unsigned char *)tcp + tcp->doff * 4, data + offset, payload), "payload");
		for (int i = 0; i < payload; i++)
			if (!seen[offset + i]) { seen[offset + i] = 1; received++; }
	}
	if (sack) {
		/* SACK only the middle MSS of the outstanding GSO skb. */
		reply(base_seq, 0, 0, 1);
		usleep(20000);
		struct tcp_info info;
		socklen_t info_len = sizeof(info);
		check(!getsockopt(sock, IPPROTO_TCP, TCP_INFO, &info, &info_len), "TCP_INFO");
		check(info.tcpi_sacked == 1 && info.tcpi_unacked == 4, "SACK packet accounting");
		printf("RTX_ACCOUNT %s unacked=%u sacked=%u\n", name, info.tcpi_unacked, info.tcpi_sacked);
		if (scenario == 5) {
			/* Discard the SACKed middle MSS, ACK only the first MSS. */
			reply(base_seq + 1000, 0, 0, 0);
			do { tcp = receive(packet, &payload); } while (!payload);
			offset = ntohl(tcp->seq) - base_seq;
			check(offset == 1000 && offset + payload <= sizeof(data), "reneged retransmit range");
			check(!memcmp((unsigned char *)tcp + tcp->doff * 4, data + offset, payload), "reneged payload");
			duplicate = payload;
		}
	} else if (scenario == 3) {
		/* Packet behavior only; internal GSO/trim proof is separate. */
		reply(base_seq + 2000, 0, 0, 0);
		usleep(10000);
		struct tcp_info partial;
		socklen_t partial_len = sizeof(partial);
		check(!getsockopt(sock, IPPROTO_TCP, TCP_INFO, &partial, &partial_len), "partial TCP_INFO");
		check(partial.tcpi_unacked == 2 && !partial.tcpi_sacked && !partial.tcpi_lost,
		      "partial ACK packet accounting");
		printf("RTX_ACCOUNT PARTIAL acked_bytes=2000 unacked=2 sacked=0 lost=0\n");
	} else if (scenario != 4) {
		/* Withhold all ACKs until the kernel retransmits actual data. */
		do { tcp = receive(packet, &payload); } while (!payload);
		offset = ntohl(tcp->seq) - base_seq;
		check(offset + payload <= sizeof(data), "retransmit range");
		check(!memcmp((unsigned char *)tcp + tcp->doff * 4, data + offset, payload), "retransmit payload");
		duplicate = payload;
	}
	reply(base_seq + sizeof(data), 0, 0, 0);
	usleep(10000);
	struct tcp_info info;
	socklen_t info_len = sizeof(info);
	check(!getsockopt(sock, IPPROTO_TCP, TCP_INFO, &info, &info_len), "TCP_INFO after ACK");
	check(!info.tcpi_unacked && !info.tcpi_sacked && !info.tcpi_lost, "drained packet accounting");
	printf("RTX_ACCOUNT %s drained=1 total_retrans=%u\n", name, info.tcpi_total_retrans);
	if (scenario == 4) {
		/* A duplicate range below snd_una; verify TCPDSACKRecv separately. */
		reply(base_seq + sizeof(data), 0, 0, 2);
		usleep(10000);
	}
	check(!shutdown(sock, SHUT_WR), "shutdown");
	do { tcp = receive(packet, &payload); } while (!tcp->fin);
	reply(base_seq + sizeof(data) + 1, 0, 1, 0);
	struct pollfd pfd = { .fd = sock, .events = POLLIN };
	check(poll(&pfd, 1, 3000) == 1 && recv(sock, data, 1, 0) == 0, "peer FIN");
	close(sock);
	/* Drain our final ACK before the next connection. */
	tcp = receive(packet, &payload);
	check(tcp->ack && !tcp->fin && !payload, "final ACK");
	printf("RTX_PACKET %s bytes=%d retransmitted=%d PASS\n", name, received, duplicate);
}

void run_packet_cases(void)
{
	struct ifreq req = { .ifr_name = "rtx0" };
	struct sockaddr_in *addr = (void *)&req.ifr_addr;
	int ctl;

#ifndef RTX_NATIVE_PEER
	mkdir("/dev/net", 0755);
	check(!mknod("/dev/net/tun", S_IFCHR | 0600, makedev(10, 200)), "tun node");
#endif
	tun = open("/dev/net/tun", O_RDWR);
	check(tun >= 0, "tun");
	req.ifr_flags = IFF_TUN | IFF_NO_PI;
	check(!ioctl(tun, TUNSETIFF, &req), "TUNSETIFF");
	write_file("/proc/sys/net/ipv6/conf/rtx0/disable_ipv6", "1\n");
	ctl = socket(AF_INET, SOCK_DGRAM, 0);
	addr->sin_family = AF_INET;
	inet_pton(AF_INET, "198.18.0.1", &addr->sin_addr);
	check(!ioctl(ctl, SIOCSIFADDR, &req), "address");
	inet_pton(AF_INET, "255.255.255.0", &addr->sin_addr);
	check(!ioctl(ctl, SIOCSIFNETMASK, &req), "netmask");
	req.ifr_flags = IFF_UP | IFF_RUNNING;
	check(!ioctl(ctl, SIOCSIFFLAGS, &req), "interface up");
	close(ctl);
#ifdef RTX_NATIVE_PEER
	packet_case(1);
	packet_case(3);
	packet_case(4);
	packet_case(5);
#else
	check(!mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL), "debugfs");
	write_file("/sys/kernel/debug/tracing/kprobe_events", "p:rtx_fragment tcp_fragment queue=%si:u32 len=%cx:u32\nr:rtx_result tcp_fragment result=$retval:s32\n");
	write_file("/sys/kernel/debug/tracing/events/kprobes/rtx_fragment/trigger", "stacktrace\n");
	write_file("/sys/kernel/debug/tracing/events/kprobes/enable", "1\n");
	packet_case(1);
	packet_case(0);
	packet_case(2);
	write_file("/sys/kernel/debug/tracing/tracing_on", "0\n");
	int fd = open("/sys/kernel/debug/tracing/trace", O_RDONLY);
	char buffer[4096];
	ssize_t n;
	check(fd >= 0, "trace");
	puts("RTX_TRACE_BEGIN");
	while ((n = read(fd, buffer, sizeof(buffer))) > 0)
		check(write(1, buffer, n) == n, "trace output");
	check(!n, "trace read");
	puts("RTX_TRACE_END");
	close(fd);
#endif
	close(tun);
}
