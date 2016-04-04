pebble-recorder
===============
A tool for making Pebble screen recordings. Built for OS X, though with some tweaks it'll work on *nix.

## Note

This __does not__ currently support any recent version of the Pebble SDK. Significant changes have been made to the SDK folder structure and emulator configuration, meaning this code will no longer work out-of-the-box. One might investigate the new emulator debugging features to achieve smooth & lossless recording, the original goal of this tool.

## Requirements

- Python 2.7
- Pebble SDK 3
- A C compiler

## Instructions

1. Compile timestep.c - if you have OS X, use the provided Makefile.
1. Navigate to your Pebble app's directory
1. Run the `pebble-recorder` command - your app will be rebuilt and booted in the emulator (NB: Tested with Basalt only - use `pebble-recorder aplite` to live dangerously).
1. Get the app ready, then return to the console and start the recording. When you're done, use Ctrl+C to stop it. During recording, the emulator's clock is governed to ensure no frames are dropped, so it'll be slower than usual.
1. Frames will have been written (in [PBM format](https://en.wikipedia.org/wiki/Netpbm_format)) to `.pr-captures/` - use your favourite image manipulation utility to convert them into a GIF, video, etc.
