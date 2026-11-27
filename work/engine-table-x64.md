# The x64 ASIC descriptor and the device table

## What the peer's x32 patch changes

The peer's working x32 driver corrects four fields in the ASIC descriptor at x86 file `0x1FFF7C` and the
raster configuration beside it:

| field | x86 offset | stock | Hawaii |
|---|---|---|---|
| max_tile_pipes | `0x1FFF80` | 4 | 16 |
| max_shader_engines | `0x1FFFA0` | 2 | 4 |
| max_backends_per_se | `0x1FFFB0` | 0 | 4 |
| max_cu_per_sh | `0x1FFFB8` | 7 | 11 |
| PA_SC_RASTER_CONFIG | `0x1FFFC0` | `0x16000012` | `0x3A000012` |
| PA_SC_RASTER_CONFIG_1 | `0x1FFFE0` | `0` | `0x0000002E` |

The x86 miniport from the same package (`XP_INF/B166713/ati2mtag.sy_`, expanded 7,380,480 bytes) shows
those stock values at those offsets, so the table is anchored.

## What the x64 miniport already carries

The x64 miniport (`work/stageE/ati2mtag-s5_accessor_blob.sys`, 8,525,824 bytes) contains a Hawaii-shaped
descriptor at file `0x226D20`:

```
+0x04  7            +0x24  4 (max_shader_engines, Hawaii)
+0x34  4            +0x3C  11 (max_cu_per_sh, Hawaii)
+0x44  0x3A000012   (shipped raster config)
+0x64  0x0000002E   (raster config 1)
```

The stock `0x16000012` value does not occur anywhere in the x64 file, and `0x3A000012` occurs six times.
So the x64 binary's descriptor table already carries Hawaii values; the geometry does not need porting.
The `+0x04` value is 7, not the peer's 16 or stock 4, and it is the one field whose x64 meaning is not yet
settled.

## The device-table record

The `0x67B0` record is at file `0x1F13D0`, 32 bytes:

```
index 0x78   device 0x67B0   zero   length 0x28   ptr1 0x1FF570   ptr2 0x239A10
```

The `0x67B1` record at `0x1F13F0` is the same shape with index `0x78` and length `0x3C`. The supported
devices have their own records: `0x985F` at `0x1F13B0` with index `0x7D`, and `0x6900` at `0x1F1410`
with index `0x82`.

The `ptr1` destinations (`0x1FF570`, `0x1FF5F0`, `0x1FF2F0`) are zero in the file, and so is `ptr2`
(`0x239A10`). They are runtime destinations, not the static descriptor. The index is the likely selector
into the descriptor table, and `0x78` is the value to follow.

## Next

Find the code that consumes the record's index and selects the descriptor. Compare index `0x78` with the
supported devices' indices `0x7D` and `0x82`, and the descriptor at `0x226D20` against the entries those
select. The switch retarget alone did not move the engine, so the record-to-descriptor path is the next
place the 0x67B0 device can be losing its Hawaii data.

## The switch selects a clock table, not the descriptor

The device-ID switch at file `0x119D4F` (VA `0x12A74F`) subtracts `0x6640` and selects one of four
clock/voltage tables, storing the choice at `adapter+0x15C8`:

| case | table |
|---|---|
| `0x6640` | `0x32BAC0` |
| `0x6641` | `0x32BB10` |
| `0x6650`, `0x6658`, `0x665C` | `0x32BA20` |
| `0x6651`, `0x665D` | `0x32BA70` |

The tables hold 16-bit clock/voltage pairs, not pointers. The Hawaii descriptor at `0x226D20` is not
referenced by an absolute pointer anywhere in the file, and the device-table records are not either. The
record's index (`0x78` for `0x67B0`, `0x7D` for `0x985F`, `0x82` for `0x6900`) is an ASIC family id used
by a path that has not been traced yet.

The patched `0x67B0` case takes the `0x6640` case's table at `0x32BAC0`. The stock case never handles
`0x67B0`, so the patch is selecting a Bonaire-family clock table for the Hawaii device. Whether that table
is the right one is the next question, and the comparison is against the table the native Hawaii path
selects in the same-era Windows 7 x64 driver.

## The clock tables are identical

The x86 switch at file `0xD39A3` (VA `0xE43A3`) has the same base (`0x6640`) and the same case layout as
the x64 switch. Its case `0x6640` selects `0x2B19F0`, and that table is byte-for-byte identical to the
x64 case `0x6640` table `0x32BAC0`. The other three match as well:

| x86 | x64 |
|---|---|
| `0x2B1950` | `0x32BA20` |
| `0x2B19A0` | `0x32BA70` |
| `0x2B1A40` | `0x32BB10` |

So the switch patch selects the same clock/voltage data the peer's working x32 build used. The clock
table is not where the x64 build differs, and the engine idle wait has to be in the miniport's engine or
CP initialization, not in this table.

## The CP ring setup is the same in both builds

The ring function lives in `ati2cqag`, not the miniport. On x64 it runs at RVA `0x13770` onward; on x86
the same function is at RVA `0xC40E` onward. Both read `CP_RB0_CNTL` (`0xC104`), mask the buffer-size
fields, write the size back, write `CP_RB0_BASE` (`0xC100`), set the enable bit, read `CP_RB0_RPTR`
(`0x8700`), write it to `CP_RB0_WPTR` (`0xC114`), and clear the enable bit. The x64 uses the same
register offsets and the same field arithmetic as the x86.

So the ring setup is not the x64 difference, and `CP_RB0_RPTR` at `0x20` says the CP fetched the first
32-byte packet. The wait is in what the engine does with that packet, not in how the ring was armed.

## The microcode registers

The CP/PFP microcode register constants are sparse in both builds. The x64 miniport has one
`CP_PFP_UCODE_DATA` (`0xC214`) reference at RVA `0x143BF`; the x86 miniport has none. The x64 `ati2cqag`
has one `CP_PFP_UCODE_ADDR` (`0xC210`) at `0x691CA` and one `CP_ME_UCODE_ADDR` (`0xC220`) at `0x59435`;
the x86 `ati2cqag` has neither. These are single references, not a loader loop. The `0x691CA` reference
disassembles to an alignment routine (`add $0x1f; and $0xffffffe0`), not a register write, and the
`0x59435` reference lands in return code, so both are false positives. The microcode load is not visible
as a direct register constant in either `ati2cqag`.

The next probe is the packet the CP fetched. `CP_RB0_BASE` (`0xC100`) and `CP_RB0_WPTR` (`0xC114`) can
be read through the host monitor during a boot, and the ring buffer's guest virtual address is in the
`ati2cqag` context that the collector already reads. Decoding the first 32-byte packet says whether the
engine is being asked to do something the 2D block cannot complete.

## The ring is not in the visible VRAM window

The host monitor reads, with the guest at the bugcheck hold:

```
CP_RB0_BASE  0xFF740000
CP_RB0_WPTR  0x00000020
CP_RB0_RPTR  0x00000020
```

BAR 0 is guest physical `0xE0000000` to `0xEFFFFFFF`, the first 256 MiB of VRAM. The base's low 28 bits
(`0xF740000`) land inside that window, but the eight dwords there are unrelated data
(`0x61A04176`, `0x16F6A24D`, ...), not a PM4 packet. The base near `0xFF740000` sits at the top of the
GPU's 4 GB address space, in the GART or system-memory aperture, so the host monitor cannot read the ring
through BAR 0.

The packet therefore has to come through the ring buffer's guest virtual address in the `ati2cqag`
context, read with the collector at the probe5b break where `Rdx` is the adapter.

## The adapter and engine objects at the break

The probe5c break held `Rdx = 0xfffffa8000c7a000` (the adapter). The reads:

```
adapter+0x398   0xfffffa8000fbbd40   engine object
adapter+0x138   0xfffffadfaae49000   aperture
adapter+0x100   0xfffffab4d983b0     sub-object
engine+0x00     0xfffff97fff57e530   vtable
engine+0x08     0xfffffa8000c752b0
engine+0x10     0xfffffa80010c68f8
engine+0x18     0xfffffa8000c57010
engine+0x20     0xfffffa8000ac4010
engine+0x38     0xfffffa8000c7a000   the adapter
engine+0x40     0xfffffa8000fbbda0
```

The `+0x18` object at `0xfffffa8000c57010` holds a vtable (`0xfffff97fff57e048`) and two `0x7c0` sizes;
its `+0x70` and `+0x118` fields are more sub-object vtables. None of these fields is the PM4 packet, and
the ring VA is not a plain field of the engine object.

The ring base `0xFF740000` is a GART or memory-controller address. The bridge from it to the guest VA is
the engine's `0xC0` vtable slot (`0xfffff97fff57e5f0`), whose method returns the object the ring function
adds `0x18` to. Reading that slot from the file and disassembling its target is the next static step.

## The 0xC0 method is a getter

The engine vtable slot `0xC0` is RVA `0x34620`. It switches on `%edx` and returns `[rcx+0x40]` for 0,
`[rcx+0x48]` for 1, and `[rcx+0x50]` for 2. The ring function calls it with `edx=0`, so the ring object is
`[engine+0x40]` = `0xfffffa8000fbbda0`.

That object's `+0x18` is `0xfffffa8000c57010`, a sub-object with a vtable and two `0x7c0` sizes, not the
packet. The ring base the function adds is the caller's `%rdi`, and the caller reaches the function
indirectly, so the next probe is an entry break at RVA `0x1369A`. At that entry `%rdi` holds the ring base
and `%rcx`/`%rsi` hold the adapter, which gives the buffer's guest VA directly.

## The ring function is not on the failing path

The probe6 package put `int3` at RVA `0x1369A`, the ring function entry, with the checksum recomputed to
`0x000e0a23`. The module loaded at `0xfffff97fff507000` in run `engine-ring`, but no state change appeared
at the entry before the bugcheck: the first `STATE CHANGE` at `02:34:18` follows the kernel's break-in
countdown and the `0xEA` fatal line.

So the `ati2cqag` ring function runs after the wait that never passes. The `CP_RB0` ring that fetched one
32-byte packet is armed somewhere else, most likely by the miniport's register table: the `0xC100`
immediate is absent from the miniport `.text`, so the ring setup is a data table or an accessor call, not
an inlined constant. The next probe is that table or accessor.

## No pointer to the CP tables

The miniport's `.rdata` holds `0xC100` at RVA `0x14FA53` and its `.data` holds it at `0x48231F`, inside
contiguous register lists (`0xBF00`, `0xC000`, `0xC100`, `0xC200`, ...). No eight-byte pointer to either
table exists anywhere in the file (`0x49231F`, `0x492300`, `0x15FA53`, `0x15FA00` all return zero hits),
and no `.text` instruction names those addresses. So the miniport does not consume the tables by a direct
or pointer-based reference.

The `CP_RB0_BASE` seen at `0xFF740000` may therefore be armed by the VBIOS/CSM before Windows loads, or by
a computed accessor. The Windows driver's 2D engine never reaches its own ring function, which matches the
`0xEA` wait.

## The FB window is 16 MiB at 0xF4000000

`MC_VM_FB_LOCATION` (`0x2024`) reads `0xF4FFF400`: the base is `0xF4000000` and the top is `0xF4FF0000`,
a 16 MiB window. The next MC words are `0x00000000`, `0x0003FFFF`, `0x00000000`.

The ring at `0xFF740000` is above that window. The BAR 0 read at the ring-minus-base offset `0xB740000`
returns unrelated data, and the QEMU monitor has no `write` command, so the host cannot move the window
itself.

The controlled window move needs a guest-side write to the aperture: the collector has `read` only, so the
next step is a `write` command over the KD `DbgKdWriteVirtualMemoryApi` (the aperture VA plus `0x2024`),
then a BAR 0 read at offset zero for the packet.

## The window move to the ring base breaks BAR 0

The collector's new `write` command (KD `DbgKdWriteVirtualMemoryApi`, API `0x3131`) wrote `0xFF84FF74` to
`MC_VM_FB_LOCATION` through the aperture VA. The KD returned `ret=0`, but `actual=0x8` for a four-byte
payload, so the reply's actual field or the transfer length is not what the command assumes.

After the write, BAR 0 offset 0 read all ones (`0xFFFFFFFF` in every word), the vfio region read failing,
and writing the original `0xF4FFF400` back did not restore it. The move to the ring's top
(`0xFF740000`) is not a valid FB window for this device, and the aperture needs a device reset, which the
next boot's function-level reset provides.

The VBIOS ring therefore cannot be reached by moving the FB window to it. The remaining path is the
guest's own mapping of the top VRAM, if the VBIOS or the miniport creates one.
