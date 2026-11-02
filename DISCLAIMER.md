# Disclaimer

## Not affiliated with WHOOP

This is an independent, unofficial project. It is not affiliated with, endorsed by, or
sponsored by WHOOP, Inc. "WHOOP", "WHOOP 5.0" and "WHOOP MG" are trademarks of their owner. This
project uses these names only to identify the hardware that it works with. It uses no WHOOP
logo or other brand asset.

## Purpose: interoperability with your own device

The purpose of this project is to let the owner of a strap read the data of that strap, on
their own computer, in an open format. It is not made to compete with, replace, or harm the
products or services of WHOOP.

The project needs your own physical strap. It does not get access to any WHOOP account, cloud
service or subscription feature. It does not remove or go around any paywall or DRM.

## No proprietary material

This repository contains only code that its author wrote and factual notes about the Bluetooth
protocol. The protocol facts come from two sources:

1. Public community projects. The README lists them under "Sources".
2. Bluetooth traffic between the author's own strap and this tool.

The repository does not contain decompiled or disassembled WHOOP code. It does not contain,
redistribute or link to WHOOP firmware, WHOOP app code, WHOOP keys or other copyrighted assets
of WHOOP. Fixed byte sequences in the code (for example the CLIENT_HELLO frame
and the feature-flag names) are protocol facts that are necessary for the strap to answer.

## Terms of service

The WHOOP Terms of Use limit reverse engineering of WHOOP software, except where the law
permits it. The rules that apply to you depend on your country and on your agreement with
WHOOP. You are responsible for your use of this tool. If you are not sure, ask a lawyer.

## Not a medical device

The values that this tool calculates (heart rate, HRV, resting heart rate, sleep window, heart
rate zones) are approximations from published methods. They are not clinically validated. They
are not medical advice. Do not use them to diagnose, monitor or treat a medical condition. If
you have a health concern, speak to a health professional.

## Risks to your strap and your data

- The strap holds one Bluetooth bond at a time. When this tool bonds, the WHOOP app loses its
  bond and must pair again.
- The history offload acknowledges each chunk. The strap then deletes that chunk. Data that this
  tool pulls does not go to the WHOOP app or the WHOOP cloud. Keep your own copy.
- `enable-deep` writes persistent feature flags to the strap. They change which data the strap
  sends. The full effect of each flag is not known.
- The protocol is undocumented. A firmware update can change it and stop this tool.

## No warranty

The software is provided "as is", without warranty of any kind. The full terms are in
[LICENSE](LICENSE). You use it at your own risk.

## Contact

If you own rights in material in this repository and you think that it should not be here,
open an issue. The author will review it in good faith and remove material when that is
correct.
