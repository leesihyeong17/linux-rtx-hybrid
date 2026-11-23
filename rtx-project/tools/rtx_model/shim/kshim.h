/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Minimal user-space stand-ins for the kernel headers that the real
 * include/linux/{rbtree,rbtree_augmented,list}.h, lib/rbtree.c and
 * include/net/tcp_rtx.h need. Only glue lives here: no rbtree, list or
 * rtx logic is reimplemented.
 */
#ifndef RTX_MODEL_KSHIM_H
#define RTX_MODEL_KSHIM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int32_t s32;
typedef uint8_t __u8;
typedef uint16_t __u16;
typedef uint32_t __u32;
typedef uint64_t __u64;
typedef int32_t __s32;

#undef __always_inline
#define __always_inline inline __attribute__((__always_inline__))
#define __rcu

/* From include/linux/types.h. */
struct list_head {
	struct list_head *next, *prev;
};
struct hlist_head {
	struct hlist_node *first;
};
struct hlist_node {
	struct hlist_node *next, **pprev;
};
#define likely(x) __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)
#define READ_ONCE(x) (*(const volatile __typeof__(x) *)&(x))
#define WRITE_ONCE(x, val) (*(volatile __typeof__(x) *)&(x) = (val))
#define rcu_assign_pointer(p, v) WRITE_ONCE((p), (v))
#define RCU_INIT_POINTER(p, v) ((p) = (v))
#define EXPORT_SYMBOL(sym)
#define EXPORT_SYMBOL_GPL(sym)

#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))

/* Same values as include/linux/poison.h with POISON_POINTER_DELTA 0:
 * unmapped in user space, so a use after list_del() faults.
 */
#define LIST_POISON1 ((void *)0x100)
#define LIST_POISON2 ((void *)0x122)

static inline int rtx_model_ilog2(u32 n)
{
	return 31 - __builtin_clz(n);
}
#define ilog2(n) rtx_model_ilog2(n)

#define min_t(type, x, y) ({ type __x = (x); type __y = (y); __x < __y ? __x : __y; })

#define prefetch(x) __builtin_prefetch(x)

/* WARN_ON_ONCE() reports through the harness and still evaluates to the
 * condition, like the kernel macro.
 */
void rtx_model_warn(const char *file, int line, const char *cond);
#define WARN_ON_ONCE(cond) ({						\
	int __ret = !!(cond);						\
	if (__ret)							\
		rtx_model_warn(__FILE__, __LINE__, #cond);		\
	__ret;								\
})
#define WARN_ON(cond) WARN_ON_ONCE(cond)

/* Per-CPU data: one CPU. */
#define DECLARE_PER_CPU(type, name) extern __typeof__(type) name
#define DEFINE_PER_CPU(type, name) __typeof__(type) name
#define this_cpu_add(var, val) ((var) += (val))
#define this_cpu_inc(var) ((var) += 1)

#endif
