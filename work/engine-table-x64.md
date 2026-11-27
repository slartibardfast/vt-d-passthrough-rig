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
