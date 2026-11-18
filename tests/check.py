#!/usr/bin/env python3
"""Run the short-copy pass and wrapper against small Linux x86-64 ELF fixtures."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
GUARD_RESULT = "PASS: 514 guarded copies (0 through 256, both page edges)"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=dict(os.environ, LC_ALL="C"))
    if result.returncode:
        raise RuntimeError(f"Command failed: {args}\n{result.stdout}")
    return result.stdout


def disassemble(binary, symbol):
    return run("objdump", "-d", "--no-show-raw-insn", f"--disassemble={symbol}",
               binary)


def symbol_address(assembly, name):
    match = re.search(rf"^([0-9a-f]+) <{name}>:", assembly, re.M)
    require(match, f"Missing symbol {name}\n{assembly}")
    return int(match[1], 16)


def edge_addresses(assembly, instruction, target):
    return [int(address, 16) for address in re.findall(
        rf"^\s*([0-9a-f]+):\s+{instruction}\s+[^\n]*<{target}>",
        assembly, re.M)]


def fixture(directory, define=None):
    elf, fdata = directory / "input", directory / "input.fdata"
    flags = [f"-D{define}"] if define else []
    run("gcc", "-O2", "-g", "-fPIE", "-pie", "-Wl,--emit-relocs", *flags,
        HERE / "guarded-copy.c", HERE / "copy-probe.S", "-o", elf)
    copy_code = disassemble(elf, "copy_probe")
    main_code = disassemble(elf, "main")
    copy_start = symbol_address(copy_code, "copy_probe")
    main_start = symbol_address(main_code, "main")
    tails = edge_addresses(copy_code, "jmp", r"memcpy@plt")
    calls = edge_addresses(main_code, "call", "copy_probe")
    require(len(tails) == 1 and calls, "Fixture must contain a PLT tail and a call")
    fdata.write_text("".join(
        f"1 main {call - main_start:x} 1 copy_probe 0 0 514\n" for call in calls
    ) + f"1 copy_probe {tails[0] - copy_start:x} 1 memcpy@PLT 0 0 514\n")
    return elf, fdata, tails[0]


def length_profile(path, elf, tail, total=10000, short=9000, wrong_hash=False):
    digest = "0" * 64 if wrong_hash else hashlib.sha256(elf.read_bytes()).hexdigest()
    # Two populated buckets are enough to test the admission threshold.
    counts = [0] * 17
    counts[1] = short // 2
    counts[16] = short - counts[1]
    row = " ".join(map(str, [total, *counts]))
    path.write_text(f"bolt-memcpy-profile-v1 {digest}\n{tail:x} {row}\n")
    return path


def rewrite(bolt, directory, elf, fdata, profile, eligible):
    output = directory / f"{profile.stem}.elf"
    log = run(bolt, elf, f"-data={fdata}", "-o", output, "--strict",
              "--verify-cfg", "--reorder-blocks=ext-tsp", "--thread-count=1",
              "-v=1", f"--memcpy-short-profile={profile}",
              "--memcpy-reuse-length-branch")
    require(f"specialized {int(eligible)} memcpy tail call sites" in log, log)
    return output, log


def fallback(bolt, utils, directory, elf, fdata, profile, reason):
    run(sys.executable, utils / "bolt_safe_layout.py", "--bolt", bolt,
        "--input", elf, "--data", fdata, "--length-profile", profile,
        "--directory", directory)
    plan = json.loads((directory / "plan.json").read_text())
    require(plan["mode"] == "off" and plan["reason"] == reason, str(plan))
    require((directory / "off").read_bytes() == (directory / "selected").read_bytes(),
            "The selected file must be byte-identical to off")


def check(bolt, utils, directory):
    elf, fdata, tail = fixture(directory)
    valid = length_profile(directory / "exact-threshold.profile", elf, tail)
    rewritten, log = rewrite(bolt, directory, elf, fdata, valid, True)
    require("reused memcpy length branch in copy_probe" in log, log)
    assembly = disassemble(rewritten, "copy_probe")
    branch = r"\bcmp\s+\$0x10,%rdx\s*\n\s*[0-9a-f]+:\s+"
    direct_tail = branch + r"ja\s+[^\n]*<memcpy@plt>"
    fallthrough_tail = branch + r"jbe\s+[^\n]*\n\s*[0-9a-f]+:\s+jmp\s+[^\n]*<memcpy@plt>"
    require(re.search(direct_tail, assembly) or re.search(fallthrough_tail, assembly),
            assembly)
    for binary in (elf, rewritten):
        require(run(binary).strip() == GUARD_RESULT, f"Unexpected output from {binary}")
    print(GUARD_RESULT + " on input and rewritten ELF")
    print("PASS: unsigned length cutoff at 16 and retained libc tail")

    for name, total, short in (("too-few", 9999, 9999), ("too-long", 10000, 8999)):
        profile = length_profile(directory / f"{name}.profile", elf, tail, total, short)
        rewrite(bolt, directory, elf, fdata, profile, False)
    print("PASS: 10,000 sample and 90% short-copy admission thresholds")

    for define in ("CHANGED_LENGTH", "NARROW_CHECK"):
        case = directory / define
        case.mkdir()
        bad_elf, bad_fdata, bad_tail = fixture(case, define)
        profile = length_profile(case / "length.profile", bad_elf, bad_tail)
        _, log = rewrite(bolt, case, bad_elf, bad_fdata, profile, False)
        require("no reusable length branch" in log, log)
        fallback(bolt, utils, case / "fallback", bad_elf, bad_fdata, profile,
                 "this implementation requires one unambiguous eligible function")
        print(f"PASS: {define} rejected and fell back to identical off ELF")

    wrong = length_profile(directory / "wrong-elf.profile", elf, tail, wrong_hash=True)
    rejected = directory / "wrong-elf"
    fallback(bolt, utils, rejected, elf, fdata, wrong,
             "BOLT rejected the discovery build")
    require("memcpy profile SHA-256 does not match input ELF" in
            (rejected / "discovery.bolt.log").read_text(), "Expected ELF hash rejection")
    print("PASS: profile for a different ELF fell back to identical off ELF")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bolt", required=True, type=lambda p: Path(p).resolve())
    parser.add_argument("--utils", required=True, type=lambda p: Path(p).resolve(),
                        help="Directory containing bolt_safe_layout.py and elfaudit.py")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bolt-short-test-") as tmp:
        check(args.bolt, args.utils, Path(tmp))
