# File formats

## `.lvf` — Level Video Format

All integers are little-endian.

### Header (35 bytes, unencrypted)

| Field | Type | Notes |
|---|---|---|
| magic | 4 bytes | `"LVF1"` |
| version | uint8 | starts at 1 |
| width | uint16 | |
| height | uint16 | |
| framerate | float32 | e.g. 30.0, 59.94 |
| codec | uint8 | 0=H.264, 1=VP9, 2=AV1 |
| frame_count | uint32 | |
| enc_flag | uint8 | 0=none (debug), 1=AES-256-CTR, 2=AES-256-GCM |
| iv_base | 16 bytes | base IV/nonce, derived per-block |

### Index table (unencrypted)

| Field | Type |
|---|---|
| entry_count | uint32 |
| offset[entry_count] | uint64 each — absolute byte offset of each block |

### Blocks

Each block is one group of frames between two keyframes of the inner
codec, encrypted independently. Per-block IV = `iv_base XOR block_index`
(as a 128-bit big-endian integer). Block content is the raw encoded video
elementary stream for that frame range — nothing else.

Forward-compat rule: any future format revision only adds new *trailing*
fields and bumps `version`; readers ignore unknown trailing fields instead
of failing outright.

## `.laf` — Level Audio Format

Unencrypted (kept plain for fast reads by playback code). All integers
little-endian.

| Field | Type | Notes |
|---|---|---|
| magic | 4 bytes | `"LAF1"` |
| codec | uint8 | 0=FLAC, 1=Opus |
| sample_rate | uint32 | |
| channels | uint8 | |
| duration | float32 | seconds |
| stream_size | uint32 | length in bytes of what follows |
| stream | `stream_size` bytes | the FLAC/Opus file exactly as produced by the encoder — untouched |

Filename convention inside a ROM: `{id}_lvlaud.laf`, paralleling
`{id}_lvlvid_{dif}.lvf` and `{id}_lvldata_{dif}.pld`.
