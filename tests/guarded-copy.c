#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern void *copy_probe(void *dst, const void *src, size_t len);

static unsigned char *guarded_page(size_t size) {
    unsigned char *region = mmap(NULL, 3 * size, PROT_NONE,
                                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED ||
        mprotect(region + size, size, PROT_READ | PROT_WRITE) != 0) {
        perror("guarded page");
        exit(1);
    }
    return region + size;
}

/* An independent nonoverlapping source and destination for each copy. */
int main(void) {
    const long page_size = sysconf(_SC_PAGESIZE);
    if (page_size < 256)
        return 1;
    const size_t size = (size_t)page_size;
    unsigned char *src = guarded_page(size);
    unsigned char *dst = guarded_page(size);
    for (size_t i = 0; i < size; ++i)
        src[i] = (unsigned char)(i * 37 + 11);
    if (mprotect(src, size, PROT_READ) != 0) {
        perror("mprotect source");
        return 1;
    }

    for (int at_end = 0; at_end <= 1; ++at_end) {
        for (size_t n = 0; n <= 256; ++n) {
            const size_t start = at_end ? size - n : 0;
            memset(dst, 0xa5, size);
            if (copy_probe(dst + start, src + start, n) != dst + start) {
                fprintf(stderr, "Wrong return pointer at length %zu\n", n);
                return 1;
            }
            for (size_t i = 0; i < size; ++i) {
                const unsigned char want =
                    i >= start && i < start + n ? src[i] : 0xa5;
                if (dst[i] != want) {
                    fprintf(stderr, "Wrong byte at %zu, length %zu, end %d\n",
                            i, n, at_end);
                    return 1;
                }
            }
        }
    }

    puts("PASS: 514 guarded copies (0 through 256, both page edges)");
    return 0;
}
