// SPDX-License-Identifier: GPL-2.0
/* Ordinary-process entry point; only inside a dedicated network namespace. */
#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

void run_packet_cases(void);

static void timeout_exit(int sig)
{
	(void)sig;
	const char msg[] = "RTX_NATIVE TIMEOUT\n";
	ssize_t ignored = write(2, msg, sizeof(msg) - 1);
	(void)ignored;
	_exit(124);
}

int main(void)
{
	struct stat current, init;
	if (getpid() == 1 || stat("/proc/self/ns/net", &current) ||
	    stat("/proc/1/ns/net", &init) ||
	    (current.st_dev == init.st_dev && current.st_ino == init.st_ino)) {
		fputs("Run only as an ordinary process in a dedicated network namespace.\n", stderr);
		return 2;
	}
	setvbuf(stdout, NULL, _IONBF, 0);
	signal(SIGALRM, timeout_exit);
	alarm(30);
	run_packet_cases();
	puts("RTX_NATIVE PACKETS_COMPLETE (GSO/trim-path evidence still required)");
	return 0;
}
