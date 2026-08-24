# FT Sensor Zero Set

Utility for FT sensor zero-set through RPC SSH access. It checks 48V power and EMO state before running zero-set.

## Environment

Tested on **Ubuntu 22.04 (Jammy)** with an **x86_64 Ubuntu build PC**.

The Jetson UPC binary is cross-compiled on the x86 build PC for `aarch64`.

| Runtime target | Architecture | Output |
| --- | --- | --- |
| External PC | x86_64 / amd64 | `ft_zeroset_x86` |
| UPC(Jetson) | arm64 / aarch64 | `ft_zeroset_jetson` |

Run all commands from the repository root.

```bash
cd /path/to/rby1_ft_zeroset
```

## Install

Run once on the x86 Ubuntu build PC.

```bash
./install_deps_ubuntu22.04.sh
```

This installs the native x86 build tools, the `aarch64` cross-compiler, OpenSSL/zlib development packages for both x86 and arm64, and downloads `libssh` to `../libssh` if it is not already present.

Package versions are not pinned. Use the latest packages from the same Ubuntu release (`jammy`, `jammy-updates`, and `jammy-security`).

## Build

Build both binaries.

```bash
./build.sh
```

Build only one target if needed.

```bash
./build.sh x86
./build.sh jetson
```

The build script compiles static `libssh` internally and disables GSSAPI support (`WITH_GSSAPI=OFF`) to avoid static link errors such as unresolved `gss_*` symbols.

Expected output:

- `ft_zeroset_x86`: `x86-64`
- `ft_zeroset_jetson`: `ARM aarch64`

## RUN

External x86 PC:

```bash
./ft_zeroset_x86 <rpc_ip>
```

Jetson UPC:

```bash
./ft_zeroset_jetson 192.168.30.1
```

Do not run the binary through `bash`. Run it directly.

Runtime behavior:

- Checks 48V power on `can3` before zero-set.
- Exits without sending a 48V command if EMO is pressed.
- If 48V is initially off, turns it on before zero-set and turns it off again after the result is reported.
- If 48V is initially on, keeps it on after zero-set.
- Performs zero-set only on newer FT sensor firmware; legacy firmware is reported as `LEGACY`.

## Troubleshooting

If `apt update` reports that a third-party repository does not support `arm64`, add `[arch=amd64]` to that repository's `deb` line.
