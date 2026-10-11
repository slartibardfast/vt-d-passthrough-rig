/* Fakes modem-control and serial-structure ioctls that PTY slaves reject with
 * ENOTTY, so Wine's serial layer can drive a PTY as a COM port for kernel
 * debugging. Everything else passes through to the real ioctl. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

/* x86 tty ioctl commands (asm-generic/ioctls.h values) */
#define SH_TIOCMBIS 0x5416
#define SH_TIOCMBIC 0x5417
#define SH_TIOCMSET 0x5418
#define SH_TIOCMGET 0x541D
#define SH_TIOCGSERIAL 0x541E
#define SH_TIOCSSERIAL 0x541F

/* linux/serial.h serial_struct, enough of it for TIOCGSERIAL */
struct sh_serial_struct {
    int type;
    int line;
    unsigned int port;
    int irq;
    int flags;
    int xmit_fifo_size;
    int custom_divisor;
    int baud_base;
    unsigned short close_delay;
    char io_type;
    char reserved_char[1];
    int hub6;
    unsigned short closing_wait;
    unsigned short closing_wait2;
    unsigned char *iomem_base;
    unsigned short iomem_reg_shift;
    unsigned int port_high;
    unsigned long iomap_base;
};

int ioctl(int fd, unsigned long req, ...)
{
    static int (*real_ioctl)(int, unsigned long, ...) = 0;
    void *arg;
    va_list ap;

    if (!real_ioctl)
        real_ioctl = dlsym(RTLD_NEXT, "ioctl");

    va_start(ap, req);
    arg = va_arg(ap, void *);
    va_end(ap);

    int r = real_ioctl(fd, req, arg);
    if (r == -1 && (errno == ENOTTY || errno == EINVAL)) {
        switch (req) {
        case SH_TIOCMBIS:
        case SH_TIOCMBIC:
        case SH_TIOCMSET:
            return 0;
        case SH_TIOCMGET: {
            unsigned int *m = (unsigned int *)arg;
            if (m) {
                *m = 0x03 | 0x80 | 0x40;   /* DTR|RTS set, CTS|DSR present */
                return 0;
            }
            break;
        }
        case SH_TIOCGSERIAL: {
            struct sh_serial_struct *s = (struct sh_serial_struct *)arg;
            if (s) {
                s->type = 13;              /* PORT_16550A */
                s->line = 0;
                s->port = 0x3f8;
                s->irq = 4;
                s->flags = 0;
                s->xmit_fifo_size = 16;
                s->custom_divisor = 0;
                s->baud_base = 115200;
                s->close_delay = 50;
                s->io_type = 1;            /* PORT_IO */
                s->hub6 = 0;
                s->closing_wait = 3000;
                s->closing_wait2 = 3000;
                s->iomem_base = 0;
                s->iomem_reg_shift = 0;
                s->port_high = 0;
                s->iomap_base = 0;
                return 0;
            }
            break;
        }
        case SH_TIOCSSERIAL:
            return 0;
        default:
            break;
        }
    }
    return r;
}
