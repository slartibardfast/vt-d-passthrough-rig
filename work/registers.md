# The register at aperture offset 0x8010

`0x8010` is **GRBM_STATUS**, the Graphics Ring Bus Manager status word, in the GRBM block of the GC
register aperture. The R9 290X is Hawaii, which is CIK (gfx7), so the CIK layout below applies. The
vendor 2D accessor at RVA `0x2F6F0` reads this offset from the adapter's aperture base and caches it to
the global at RVA `0xDD6E8`; the poll at RVA `0x36AD0` waits for the bit to clear.

## Which aperture holds it

On Bonaire and later chips the register aperture is **BAR 5**; BAR 2 is the doorbell window and BAR 0 is
VRAM. Linux v3.17 states the split in `radeon_device.c`: `if (rdev->family >= CHIP_BONAIRE) rdev->rmmio_base =
pci_resource_start(pdev, 5);` with `rdev->doorbell.base = pci_resource_start(pdev, 2)`. QEMU's Bonaire and
Hawaii reset quirk reads `bars[5]` for the same reason. On this host BAR 5 is 256 KiB, which is the
register aperture's size, while BAR 0 is 256 MiB of VRAM and BAR 2 is 8 MiB of doorbells.

## Bits that matter for this campaign

| bit | field | notes |
|-----|-------|-------|
| 31 | GUI_ACTIVE | the bit the engine-idle wait polls; set means the GUI front end reports itself active |
| 30 | CB_BUSY | colour buffer busy |
| 29 | CP_BUSY | command processor busy |
| 28 | CP_COHERENCY_BUSY | |
| 26 | DB_BUSY | depth buffer busy |
| 25 | PA_BUSY | |
| 24 | SC_BUSY | |
| 23 | BCI_BUSY | |
| 22 | SPI_BUSY | |
| 21 | WD_BUSY | |
| 20 | SX_BUSY | |
| 19 | IA_BUSY | |
| 18 | IA_BUSY_NO_DMA | |
| 17 | VGT_BUSY | |
| 16 | WD_BUSY_NO_DMA | |
| 15 | GDS_BUSY | |
| 14 | TA_BUSY | |
| 13 | CB_CLEAN | colour buffer has no pending work |
| 12 | DB_CLEAN | depth buffer has no pending work |
| 9 | GDS_DMA_RQ_PENDING | |
| 8 | ME0PIPE0_PF_RQ_PENDING | |
| 7 | ME0PIPE0_CF_RQ_PENDING | |
| 5 | SRBM_RQ_PENDING | steady state on this silicon; not a fault flag |
| 3:0 | CMDFIFO_AVAIL | free command-fifo slots |

Nothing is defined at bit 27 in this register; the earlier note that called the polled bit "bit 27 of a
status field" was reading a shift applied to a different value, or the SE word at `0x8014`, where bit 27
is `SE_SPI_BUSY`.

## The neighbours read alongside it

| offset | register | use here |
|--------|----------|----------|
| `0x8000` | GRBM_CNTL | `GRBM_READ_TIMEOUT` in the low bits |
| `0x8008` | GRBM_STATUS2 | bit 30 `CPG_BUSY`, bit 29 `CPC_BUSY`, bit 28 `CPF_BUSY`, bit 25 `TC_BUSY`, bit 24 `RLC_BUSY` |
| `0x8014` | GRBM_STATUS_SE0 | bit 2 `SE_CB_CLEAN`, bit 1 `SE_DB_CLEAN` |
| `0x8018` | GRBM_STATUS_SE1 | same layout |
| `0x8020` | GRBM_SOFT_RESET | bit 0 CP, bit 2 RLC, bit 16 GFX |
| `0x8060` | GRBM_INT_CNTL | bit 19 `GUI_IDLE_INT_ENABLE`, the interrupt twin of the poll |
| `0x8680` | CP_STAT | bit 31 `CP_BUSY` |
| `0x8700` | CP_RB0_RPTR | ring read pointer |
| `0xC100` | CP_RB0_BASE | ring base |
| `0xC10C` | CP_RB0_RPTR_ADDR | where the CP writes the read pointer |
| `0xC114` | CP_RB0_WPTR | ring write pointer |

## The two words this campaign measured

| word | state | decode |
|------|-------|--------|
| `0x00003028` | the boot that withholds the grafted display package | `CMDFIFO_AVAIL` 8, `SRBM_RQ_PENDING`, `DB_CLEAN`, `CB_CLEAN`; `GUI_ACTIVE` and `CP_BUSY` clear |
| `0xA0003028` | the boot that loads the grafted package at 1024x768x32@60 | the same word with `GUI_ACTIVE` and `CP_BUSY` raised |

The public record carries both exact words on CIK hardware: `0xA0003028` on a Mullins APU mid-lockup with
a fence timeout, and `0x00003028` on an R9 270X after a successful soft reset. The bit a driver waits on
is `GUI_ACTIVE`: amdgpu's `gfx_v7_0_wait_for_idle` loops on `GRBM_STATUS__GUI_ACTIVE_MASK` and returns
`-ETIMEDOUT`, and `r600_gui_idle()` returns false while it is set. `SRBM_RQ_PENDING` is never polled by
either driver, and it is set in the publicly healthy post-reset dumps, so it is steady state.

A dead or unresponsive aperture does not read like either word: QEMU's reset quirk treats `0xffffffff`
from this BAR as "still in reset", so all-ones is the published no-answer value.

## What this does not settle

Whether the grafted Windows stack maps this same aperture, and whether it trusts the same bit, is not
established by naming the register. The loaded x64 stack is an APU-shaped package on a discrete Hawaii
card, and that mismatch is a live candidate for a wait that never clears.

## Sources

- `drivers/gpu/drm/radeon/cikd.h`, CIK register and bit definitions:
  https://github.com/torvalds/linux/blob/v3.17/drivers/gpu/drm/radeon/cikd.h
- `drivers/gpu/drm/radeon/radeon_device.c`, the BAR 5 aperture choice:
  https://github.com/torvalds/linux/blob/v3.17/drivers/gpu/drm/radeon/radeon_device.c
- `drivers/gpu/drm/amd/amdgpu/gfx_v7_0.c`, `gfx_v7_0_wait_for_idle` on `GUI_ACTIVE`
- QEMU `hw/vfio/pci-quirks.c`, the Bonaire and Hawaii reset quirk reading `bars[5]`
- Mesa `si_gpu_load.c`, which counts GPU busy from `GUI_ACTIVE`
- Public lockup dump with `0xA0003028`: https://bbs.archlinux.org/viewtopic.php?id=313964
- Public post-reset dump with `0x00003028`: http://www.mail-archive.com/dri-devel@lists.freedesktop.org/msg113082.html
