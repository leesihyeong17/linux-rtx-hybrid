// SPDX-License-Identifier: GPL-2.0
/*
 * C (#15): multi-connection SACK stress as a guest init.
 *
 * NCONN kernel TCP senders (one thread each) write a byte pattern to
 * 198.18.0.2 through tun. This program is the receiver: a small TCP
 * endpoint on the tun side that drops and reorders data segments, answers
 * with SACK and DSACK blocks, checks every payload byte and closes with FIN.
 * Losses are repaired only by the kernel's own RACK/TLP/RTO timers and fast
 * retransmit; nothing here calls into the kernel queue. With -smp 2 the
 * senders and the ACK path (tun write -> softirq) run on different CPUs.
 *
 * Output: CSTRESS lines, /proc/rtx_stats and TcpExt deltas, then power off.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/if.h>
#include <linux/if_tun.h>
#include <linux/reboot.h>
#include <netinet/ip.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define NCONN		8
#define BYTES		(1024 * 1024)
#define PORT		9000
#define MSS		1000
#define MAXR		64	/* out-of-order ranges per connection */

struct range { uint32_t s, e; };

struct conn {
	int used, done, fin_sent, fin_acked;
	uint16_t lport;			/* sender's port, network order */
	uint32_t irs, rcv_nxt, iss, snd_nxt;
	struct range ooo[MAXR];
	int nooo;
	uint32_t last_s, last_e;	/* most recently changed block */
	uint64_t bytes, bad, dups, drops, reorders;
	int burst;			/* segments still to drop */
};

static int tun;
static struct conn conns[NCONN];
static unsigned int rnd = 12345;
static int sender_ok[NCONN];
static unsigned char held[2048];
static int held_len;

static unsigned int rand_next(void)
{
	rnd = rnd * 1103515245u + 12345u;
	return rnd >> 8;
}

static uint8_t pattern(int c, uint32_t off)
{
	return (uint8_t)(off * 131 + c * 17 + 1);
}

static uint16_t csum(const void *data, size_t len, uint32_t sum)
{
	const uint8_t *p = data;

	for (; len > 1; len -= 2, p += 2)
		sum += (p[0] << 8) | p[1];
	if (len)
		sum += p[0] << 8;
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return htons(~sum);
}

static void write_file(const char *path, const char *val)
{
	int fd = open(path, O_WRONLY);

	if (fd >= 0) {
		if (write(fd, val, strlen(val)) < 0)
			perror(path);
		close(fd);
	}
}

static void dump(const char *tag, const char *path)
{
	static char buf[65536];
	int fd = open(path, O_RDONLY);
	ssize_t n;

	if (fd < 0)
		return;
	n = read(fd, buf, sizeof(buf) - 1);
	close(fd);
	buf[n > 0 ? n : 0] = 0;
	for (char *l = strtok(buf, "\n"); l; l = strtok(NULL, "\n"))
		printf("%s %s\n", tag, l);
}

/* Send one segment from 198.18.0.2:PORT+c to the sender. */
static void xmit(int c, uint8_t flags, uint32_t seq, uint32_t ack,
		 const uint8_t *opt, int optlen)
{
	unsigned char pkt[128] = {0};
	struct iphdr *ip = (void *)pkt;
	struct tcphdr *th = (void *)(pkt + sizeof(*ip));
	int tlen = sizeof(*th) + optlen;
	uint32_t pseudo;

	ip->version = 4;
	ip->ihl = 5;
	ip->tot_len = htons(sizeof(*ip) + tlen);
	ip->ttl = 64;
	ip->protocol = IPPROTO_TCP;
	inet_pton(AF_INET, "198.18.0.2", &ip->saddr);
	inet_pton(AF_INET, "198.18.0.1", &ip->daddr);
	ip->check = csum(ip, sizeof(*ip), 0);
	th->source = htons(PORT + c);
	th->dest = conns[c].lport;
	th->seq = htonl(seq);
	th->ack_seq = htonl(ack);
	th->doff = tlen / 4;
	((uint8_t *)th)[13] = flags;
	th->window = htons(65535);
	memcpy(th + 1, opt, optlen);
	pseudo = (ntohl(ip->saddr) >> 16) + (ntohl(ip->saddr) & 0xffff) +
		 (ntohl(ip->daddr) >> 16) + (ntohl(ip->daddr) & 0xffff) +
		 IPPROTO_TCP + tlen;
	th->check = csum(th, tlen, pseudo);
	if (write(tun, pkt, sizeof(*ip) + tlen) < 0)
		perror("tun write");
}

/* Add [s, e) and merge overlapping or touching ranges. */
static void add_range(struct conn *cn, uint32_t s, uint32_t e)
{
	int i, j, merged = 1;

	if (cn->nooo == MAXR)
		return;
	cn->ooo[cn->nooo++] = (struct range){ s, e };
	while (merged) {
		merged = 0;
		for (i = 0; i < cn->nooo && !merged; i++)
			for (j = i + 1; j < cn->nooo && !merged; j++) {
				struct range *r = &cn->ooo[i], *q = &cn->ooo[j];

				if ((int32_t)(r->e - q->s) < 0 ||
				    (int32_t)(q->e - r->s) < 0)
					continue;
				if ((int32_t)(q->s - r->s) < 0)
					r->s = q->s;
				if ((int32_t)(q->e - r->e) > 0)
					r->e = q->e;
				*q = cn->ooo[--cn->nooo];
				merged = 1;
			}
	}
	for (i = 0; i < cn->nooo; i++)
		if ((int32_t)(s - cn->ooo[i].s) >= 0 &&
		    (int32_t)(e - cn->ooo[i].e) <= 0) {
			cn->last_s = cn->ooo[i].s;
			cn->last_e = cn->ooo[i].e;
		}
}

static int covered(struct conn *cn, uint32_t s, uint32_t e)
{
	if ((int32_t)(e - cn->rcv_nxt) <= 0)
		return 1;
	for (int i = 0; i < cn->nooo; i++)
		if ((int32_t)(s - cn->ooo[i].s) >= 0 &&
		    (int32_t)(e - cn->ooo[i].e) <= 0)
			return 1;
	return 0;
}

static void advance(struct conn *cn)
{
	int moved = 1;

	while (moved) {
		moved = 0;
		for (int i = 0; i < cn->nooo; i++)
			if ((int32_t)(cn->ooo[i].s - cn->rcv_nxt) <= 0) {
				if ((int32_t)(cn->ooo[i].e - cn->rcv_nxt) > 0)
					cn->rcv_nxt = cn->ooo[i].e;
				cn->ooo[i] = cn->ooo[--cn->nooo];
				moved = 1;
				break;
			}
	}
}

/* ACK with an optional DSACK block first, then up to 3 SACK blocks. */
static void send_ack(int c, int dsack, uint32_t ds, uint32_t de)
{
	struct conn *cn = &conns[c];
	uint8_t opt[40];
	uint32_t v[8];
	int nb = 0, i, len;

	if (dsack) {
		v[2 * nb] = ds;
		v[2 * nb + 1] = de;
		nb++;
	}
	if (cn->nooo && (int32_t)(cn->last_e - cn->rcv_nxt) > 0) {
		v[2 * nb] = cn->last_s;
		v[2 * nb + 1] = cn->last_e;
		nb++;
	}
	for (i = 0; i < cn->nooo && nb < 3; i++) {
		if (cn->ooo[i].s == cn->last_s)
			continue;
		v[2 * nb] = cn->ooo[i].s;
		v[2 * nb + 1] = cn->ooo[i].e;
		nb++;
	}
	len = 0;
	if (nb) {
		opt[len++] = TCPOPT_NOP;
		opt[len++] = TCPOPT_NOP;
		opt[len++] = TCPOPT_SACK;
		opt[len++] = 2 + 8 * nb;
		for (i = 0; i < 2 * nb; i++) {
			uint32_t be = htonl(v[i]);

			memcpy(opt + len, &be, 4);
			len += 4;
		}
	}
	xmit(c, TH_ACK, cn->snd_nxt, cn->rcv_nxt, opt, len);
}

static void handle(const unsigned char *pkt, int n, int may_hold)
{
	const struct iphdr *ip = (const void *)pkt;
	const struct tcphdr *th;
	const uint8_t *data;
	struct conn *cn;
	uint32_t seq, end;
	int c, len, i;

	if (n < (int)sizeof(*ip) || ip->protocol != IPPROTO_TCP)
		return;
	th = (const void *)(pkt + ip->ihl * 4);
	c = ntohs(th->dest) - PORT;
	if (c < 0 || c >= NCONN)
		return;
	cn = &conns[c];
	data = (const uint8_t *)th + th->doff * 4;
	len = n - (int)(data - pkt);
	seq = ntohl(th->seq);

	if (th->syn) {
		static const uint8_t synopt[] = {
			TCPOPT_MAXSEG, 4, MSS >> 8, MSS & 0xff,
			TCPOPT_NOP, TCPOPT_WINDOW, 3, 7,
			TCPOPT_NOP, TCPOPT_NOP, TCPOPT_SACK_PERMITTED, 2,
		};

		cn->used = 1;
		cn->lport = th->source;
		cn->irs = seq;
		cn->rcv_nxt = seq + 1;
		cn->iss = 1000000u * (c + 1);
		cn->snd_nxt = cn->iss + 1;
		xmit(c, TH_SYN | TH_ACK, cn->iss, cn->rcv_nxt, synopt,
		     sizeof(synopt));
		return;
	}
	if (!cn->used)
		return;
	if (th->ack && cn->fin_sent && ntohl(th->ack_seq) == cn->snd_nxt)
		cn->fin_acked = 1;
	if (len <= 0 && !th->fin)
		return;

	if (len > 0 && !th->fin) {
		/* 2% random loss, plus rare bursts of 8 that need RTO/TLP */
		if (!cn->burst && rand_next() % 1000 < 3)
			cn->burst = 8;
		if (cn->burst || rand_next() % 100 < 2) {
			if (cn->burst)
				cn->burst--;
			cn->drops++;
			return;
		}
		if (may_hold && !held_len && rand_next() % 100 < 2) {
			memcpy(held, pkt, n);		/* 2% reordering */
			held_len = n;
			cn->reorders++;
			return;
		}
	}
	end = seq + len;
	if (len > 0) {
		if (covered(cn, seq, end)) {
			cn->dups++;
			/* in-window dup: its range goes second (OFO DSACK) */
			for (i = 0; i < cn->nooo; i++)
				if ((int32_t)(seq - cn->ooo[i].s) >= 0 &&
				    (int32_t)(end - cn->ooo[i].e) <= 0) {
					cn->last_s = cn->ooo[i].s;
					cn->last_e = cn->ooo[i].e;
				}
			send_ack(c, 1, seq, end);	/* DSACK */
			return;
		}
		for (i = 0; i < len; i++)
			if (data[i] != pattern(c, seq + i - cn->irs - 1))
				cn->bad++;
		cn->bytes += len;
		add_range(cn, seq, end);
		advance(cn);
	}
	if (th->fin && end == cn->rcv_nxt) {
		cn->rcv_nxt++;
		cn->done = 1;
		send_ack(c, 0, 0, 0);
		if (!cn->fin_sent) {
			xmit(c, TH_FIN | TH_ACK, cn->snd_nxt, cn->rcv_nxt,
			     NULL, 0);
			cn->snd_nxt++;
			cn->fin_sent = 1;
		}
		return;
	}
	send_ack(c, 0, 0, 0);
}

static void *sender(void *arg)
{
	int c = (int)(long)arg, s, one = 1;
	struct sockaddr_in a = { .sin_family = AF_INET,
				 .sin_port = htons(PORT + c) };
	static __thread unsigned char buf[65536];
	uint32_t off = 0;
	char b;

	inet_pton(AF_INET, "198.18.0.2", &a.sin_addr);
	s = socket(AF_INET, SOCK_STREAM, 0);
	setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
	if (connect(s, (void *)&a, sizeof(a))) {
		perror("connect");
		return NULL;
	}
	while (off < BYTES) {
		uint32_t n = 1000 + (off * 7 + c * 977) % 30000, i;

		if (n > BYTES - off)
			n = BYTES - off;
		for (i = 0; i < n; i++)
			buf[i] = pattern(c, off + i);
		if (write(s, buf, n) != (ssize_t)n) {
			perror("write");
			return NULL;
		}
		off += n;
	}
	shutdown(s, SHUT_WR);
	sender_ok[c] = read(s, &b, 1) == 0;
	close(s);
	return NULL;
}

static void *peer(void *arg __attribute__((unused)))
{
	unsigned char pkt[70000];
	struct pollfd pfd = { .events = POLLIN };
	int n, idle = 0;

	pfd.fd = tun;
	for (;;) {
		int all = 1;

		for (int c = 0; c < NCONN; c++)
			all &= conns[c].done && conns[c].fin_acked;
		if (all)
			break;
		if (poll(&pfd, 1, 50) != 1) {
			/* flush a held (reordered) segment when idle */
			if (held_len) {
				n = held_len;
				held_len = 0;
				handle(held, n, 0);
			}
			/*
			 * 300 s without traffic. A backed-off RTO may wait
			 * TCP_RTO_MAX (120 s) plus timer-wheel slack, so the
			 * limit must stay well above that.
			 */
			if (++idle > 6000) {
				dump("CSTRESS_IDLE_TCP", "/proc/net/tcp");
				break;
			}
			continue;
		}
		idle = 0;
		n = read(tun, pkt, sizeof(pkt));
		if (n <= 0)
			continue;
		handle(pkt, n, 1);
		if (held_len && rand_next() % 4 == 0) {
			n = held_len;
			held_len = 0;
			handle(held, n, 0);
		}
	}
	return NULL;
}

int main(void)
{
	struct ifreq req = { .ifr_name = "rtx0" };
	struct sockaddr_in *addr = (void *)&req.ifr_addr;
	pthread_t pt, st[NCONN];
	struct timespec t0, t1;
	int ctl, pass = 1;

	setvbuf(stdout, NULL, _IONBF, 0);
	mkdir("/proc", 0555);
	mkdir("/sys", 0555);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	dump("CSTRESS_KERNEL", "/proc/version");
	tun = open("/dev/net/tun", O_RDWR);
	req.ifr_flags = IFF_TUN | IFF_NO_PI;
	if (tun < 0 || ioctl(tun, TUNSETIFF, &req)) {
		puts("CSTRESS FAIL tun");
		goto out;
	}
	write_file("/proc/sys/net/ipv6/conf/rtx0/disable_ipv6", "1\n");
	ctl = socket(AF_INET, SOCK_DGRAM, 0);
	addr->sin_family = AF_INET;
	inet_pton(AF_INET, "198.18.0.1", &addr->sin_addr);
	ioctl(ctl, SIOCSIFADDR, &req);
	inet_pton(AF_INET, "255.255.255.0", &addr->sin_addr);
	ioctl(ctl, SIOCSIFNETMASK, &req);
	req.ifr_flags = IFF_UP | IFF_RUNNING;
	ioctl(ctl, SIOCSIFFLAGS, &req);
	close(ctl);
	write_file("/proc/rtx_stats", "0\n");
	dump("CSTRESS_MIB_BEFORE", "/proc/net/netstat");

	clock_gettime(CLOCK_MONOTONIC, &t0);
	pthread_create(&pt, NULL, peer, NULL);
	for (long c = 0; c < NCONN; c++)
		pthread_create(&st[c], NULL, sender, (void *)c);
	for (int c = 0; c < NCONN; c++)
		pthread_join(st[c], NULL);
	pthread_join(pt, NULL);
	clock_gettime(CLOCK_MONOTONIC, &t1);

	for (int c = 0; c < NCONN; c++) {
		struct conn *cn = &conns[c];
		int ok = sender_ok[c] && cn->done && cn->fin_acked &&
			 cn->bytes == BYTES && !cn->bad &&
			 cn->rcv_nxt == cn->irs + 1 + BYTES + 1;

		printf("CSTRESS conn=%d bytes=%llu bad=%llu drops=%llu reorders=%llu dups=%llu sender=%d fin=%d/%d %s\n",
		       c, (unsigned long long)cn->bytes,
		       (unsigned long long)cn->bad,
		       (unsigned long long)cn->drops,
		       (unsigned long long)cn->reorders,
		       (unsigned long long)cn->dups, sender_ok[c], cn->done,
		       cn->fin_acked, ok ? "OK" : "FAIL");
		pass &= ok;
	}
	printf("CSTRESS seconds=%ld\n", (long)(t1.tv_sec - t0.tv_sec));
	dump("CSTRESS_MIB_AFTER", "/proc/net/netstat");
	dump("CSTRESS_STAT", "/proc/rtx_stats");
	printf("CSTRESS %s\n", pass ? "PASS" : "FAIL");
out:
	sync();
	reboot(LINUX_REBOOT_CMD_POWER_OFF);
	return 0;
}
