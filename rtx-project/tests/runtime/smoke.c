// SPDX-License-Identifier: GPL-2.0
/* Static PID 1 for the isolated QEMU guest; never runs on the host. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/reboot.h>
#include <net/if.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

static void fail(const char *what)
{
	perror(what);
	puts("RTX_RUNTIME FAIL");
	fflush(NULL);
	sync();
	reboot(LINUX_REBOOT_CMD_POWER_OFF);
	_exit(1);
}

static void timed_out(int sig)
{
	(void)sig;
	ssize_t written = write(1, "RTX_RUNTIME TIMEOUT\n", 20);
	(void)written;
	reboot(LINUX_REBOOT_CMD_POWER_OFF);
	_exit(1);
}

static void transfer(size_t size, int reset)
{
	struct sockaddr_in addr = { .sin_family = AF_INET,
		.sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
	unsigned char buf[4096];
	socklen_t alen = sizeof(addr);
	int listener, client, peer, status;
	pid_t child;
	size_t offset;
	ssize_t n;

	listener = socket(AF_INET, SOCK_STREAM, 0);
	if (listener < 0 || bind(listener, (void *)&addr, alen) ||
	    listen(listener, 1) || getsockname(listener, (void *)&addr, &alen))
		fail("listen");
	child = fork();
	if (child < 0)
		fail("fork");
	if (!child) {
		close(listener);
		client = socket(AF_INET, SOCK_STREAM, 0);
		if (client < 0 || connect(client, (void *)&addr, alen))
			_exit(2);
		if (reset) {
			buf[0] = 0x5a;
			if (send(client, buf, 1, MSG_NOSIGNAL) != 1)
				_exit(7);
			n = recv(client, buf, sizeof(buf), 0);
			_exit(n == -1 && errno == ECONNRESET ? 0 : 3);
		}
		for (offset = 0; offset < size;) {
			size_t count = size - offset;
			if (count > sizeof(buf))
				count = sizeof(buf);
			for (size_t i = 0; i < count; i++)
				buf[i] = (unsigned char)((offset + i) * 31 + 7);
			n = send(client, buf, count, MSG_NOSIGNAL);
			if (n <= 0)
				_exit(4);
			offset += n;
		}
		if (shutdown(client, SHUT_WR))
			_exit(5);
		/* A response after our FIN demonstrates working half-close. */
		if (recv(client, buf, 1, MSG_WAITALL) != 1 || buf[0] != 0xa5 ||
		    recv(client, buf, 1, 0) != 0)
			_exit(6);
		close(client);
		_exit(0);
	}
	peer = accept(listener, NULL, NULL);
	if (peer < 0)
		fail("accept");
	close(listener);
	if (reset) {
		struct linger linger = { .l_onoff = 1, .l_linger = 0 };
		if (recv(peer, buf, 1, MSG_WAITALL) != 1 || buf[0] != 0x5a)
			fail("reset handshake");
		if (setsockopt(peer, SOL_SOCKET, SO_LINGER, &linger, sizeof(linger)))
			fail("linger");
	} else {
		offset = 0;
		while ((n = recv(peer, buf, sizeof(buf), 0)) > 0) {
			for (ssize_t i = 0; i < n; i++)
				if (buf[i] != (unsigned char)((offset + i) * 31 + 7))
					fail("payload mismatch");
			offset += n;
		}
		if (n < 0 || offset != size)
			fail("payload length");
		buf[0] = 0xa5;
		if (send(peer, buf, 1, MSG_NOSIGNAL) != 1)
			fail("response");
	}
	close(peer);
	if (waitpid(child, &status, 0) != child)
		fail("waitpid");
	if (!WIFEXITED(status) || WEXITSTATUS(status)) {
		fprintf(stderr, "client wait status=%d\n", status);
		fail("client status");
	}
	printf("RTX_CASE %s bytes=%zu PASS\n", reset ? "reset" : "half-close", size);
}

#ifdef RTX_PACKET_CASES
void run_packet_cases(void);
#endif

int main(void)
{
	struct ifreq req = { .ifr_name = "lo" };
	struct utsname uts;
	int fd;

	if (getpid() != 1) {
		fputs("Run only as init in the test guest.\n", stderr);
		return 2;
	}
	setvbuf(stdout, NULL, _IONBF, 0);
	signal(SIGALRM, timed_out);
	alarm(60);
	mkdir("/proc", 0755);
	mkdir("/sys", 0755);
	if (mount("proc", "/proc", "proc", 0, NULL) ||
	    mount("sysfs", "/sys", "sysfs", 0, NULL))
		fail("mount");
	if (uname(&uts))
		fail("uname");
	printf("RTX_KERNEL %s\n", uts.release);
	fd = socket(AF_INET, SOCK_DGRAM, 0);
	req.ifr_flags = IFF_UP | IFF_LOOPBACK | IFF_RUNNING;
	if (fd < 0 || ioctl(fd, SIOCSIFFLAGS, &req))
		fail("loopback");
	close(fd);
	transfer(0, 0);
	transfer(4097, 0);
	transfer(1024 * 1024, 0);
	transfer(0, 1);
#ifdef RTX_PACKET_CASES
	run_packet_cases();
#endif
	puts("RTX_RUNTIME PASS");
	sync();
	reboot(LINUX_REBOOT_CMD_POWER_OFF);
	return 1;
}
