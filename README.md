# 复用长度判断的 BOLT 短拷贝优化

这个项目为 x86-64 ELF 上符合条件的 libc `memcpy` 尾调用增加 1–16 字节的快路径。以 `mawk` 词频统计为例，更新当前记录时会反复复制很短的词。采集用的长度 profile 中，热点调用点约 98% 的样本不超过 16 字节。对这样的调用点，几条直接读写指令可以省下进入 libc 的开销。原生 AMD 实验中，留出词频任务的耗时中位数下降 **0.39%**，95% 置信区间为 **−0.55% 至 −0.22%**。

## 提交文件

| 作业要求 | 文件 | 用途 |
| --- | --- | --- |
| 代码补丁 | [BOLT-short-memcpy-layout.patch](BOLT-short-memcpy-layout.patch) | 相对于 [LLVM 提交 `cbfac21c`](https://github.com/llvm/llvm-project/commit/cbfac21cd384f6c7c480c2d0026ecedfe2d619e7) 的改动，包含 pass、x86 指令生成、布局选项及审计脚本 |
| Docker 镜像 | [下载 BOLT-Docker-linux-amd64.tar.gz](https://github.com/Huggin423/bolt-short-memcpy-layout/releases/download/v1.0/BOLT-Docker-linux-amd64.tar.gz) | [Release v1.0](https://github.com/Huggin423/bolt-short-memcpy-layout/releases/tag/v1.0) 附件，80,481,566 字节，含 `llvm-bolt`、`mawk`、profile、输入及脚本 |
| 设计文档 | [BOLT-short-memcpy-report.pdf](BOLT-short-memcpy-report.pdf) | 问题、方案、实现、实验与局限性，导出自 [Notion 原稿](https://app.notion.com/p/41a95223c06080a4981cd2c23a33e8f4) |

验收时请从仓库主页取补丁和 PDF，从 Release v1.0 取第三项。仓库 ZIP 不包含 Release 附件。[reproduce.sh](reproduce.sh) 是助教的运行入口。[tests/](tests/) 是独立的 ELF 回归测试。[docker/Dockerfile](docker/Dockerfile) 用于重新打包镜像。

| 文件 | SHA-256 |
| --- | --- |
| `BOLT-Docker-linux-amd64.tar.gz` | `efc3430b35abb49e5ad71110593f3d6dba21a9329cbc9bf6d167c3c39cf08d3c` |
| `BOLT-short-memcpy-layout.patch` | `0d8dc1d3dad59d3acac07ff50c8cab118e509479904efbac34a36fbdbcd45146` |
| `BOLT-short-memcpy-report.pdf` | `f8142648eba22b5ba55da966dedbab9faf23dfb5ca93c38f6142502da556ca5a` |

## 优化做了什么

在固定的 LLVM 基线上，上游 BOLT 有 `--inline-memcpy` 的 x86 内联拷贝实现，以及 `--memcpy1-spec` 的单字节快路径。这里增加长度 profile 驱动的尾调用特化，复用原有的全宽零判断，让大于 16 字节的分支顺序执行并尾跳 libc。0 字节继续走原来的零长度处理。1–16 字节按 1、2–3、4–7、8–16 分组，用相应宽度访问拷贝区间的首尾，返回原目标指针。这个路径遵循标准 libc `memcpy` 的非重叠语义，短路径会绕过自定义的符号拦截实现。

完整入口为补丁里的 `bolt/utils/bolt_safe_layout.py`。安全性分两层。

| 层级 | 职责 |
| --- | --- |
| BOLT pass | `--memcpy-short-profile` 用原始 ELF 的 SHA-256 和尾指令地址绑定 profile，要求至少 10,000 个样本且至少 90% 的长度 ≤16。`--memcpy-reuse-length-branch` 要求原检查和尾调用使用可证明相同的 64 位长度值，同时检查控制流、CFI 状态和标志位活跃性。条件不满足时跳过该调用点。 |
| 外部布局脚本 | 先生成关闭短拷贝优化的 `off` 基线，再找唯一候选，重排并把新增短路径放入冷片段。比较最终 ELF 的长路径起止和尾跳地址、机器码、其余热函数的地址和大小及主 `.text` 的地址和大小。找不到唯一候选、候选构建失败、超出填充预算或检查不通过时，`selected` 取基线副本。查看 `plan.json` 的 `mode`、`reason`、`layout_checks` 和输出哈希。基线构建失败或工具异常时，命令失败退出。 |

单独运行启用优化的 `llvm-bolt` 会执行第一层。验收时请走容器入口或外部布局脚本，才能得到第二层的 ELF 检查和回退。即使长路径的地址和机器码都相同，性能仍需实测。实现面向 x86-64 ELF 上可识别的 libc 尾跳，当前布局脚本一次只接受一个可唯一定位的候选。

## 一条命令检查正确性

使用原生 **Linux x86-64**、可访问的 Docker Engine、Bash、`sha256sum`。自动下载时还需联网及 `curl`。获取仓库并进入目录后执行：

```bash
bash reproduce.sh smoke
```

脚本下载并校验归档、加载镜像，再以断网、只读根文件系统和可执行的临时目录运行。已加载且 ID 匹配时可直接复用。也可先下载附件或把文件复制到机器上，显式指定本地副本离线执行：

```bash
BOLT_ARCHIVE=/path/to/BOLT-Docker-linux-amd64.tar.gz bash reproduce.sh smoke
```

成功时应看到 `optimized the emitted layout passed all checks`、四条 `PASS output ...`、`PASS constrained layout falls back to an identical off binary` 和 `PASS emitted executables remained frozen`，退出码为 0。这表示候选通过布局审计、四组输出匹配原生实验的哈希，并且将填充预算设为 0 时，`selected` 与 `off` 逐字节相同。`smoke` 不测耗时。

## 完整性能复测

先列出容器允许绑定的逻辑 CPU，选一个空闲核。示例中的 `14` 是原实验所用 CPU，按实际环境修改。

```bash
bash reproduce.sh cpus
bash reproduce.sh bench --cpu 14 --out results
```

用空目录接收结果。脚本从同一个输入 ELF 和同一份执行 profile 生成两版程序，两版均启用 ExtTSP 及冷热分割。默认每组 144 轮，每轮按两种顺序各跑一次基线和优化版，共 **2,304 条**正式计时，每次校验输出。可用 `--blocks 1` 快速检查写出流程，完整评估按 144 轮运行。

| 负载 | 用途 | 原生 AMD 耗时变化 | 95% 置信区间 |
| --- | --- | ---: | ---: |
| `heldout` | 有效负载，181 份留出 C 源码的词频统计，合并文本读 5 遍 | −0.39% | [−0.55%, −0.22%] |
| `mixed` | 10% 短记录与 90% 长记录，合成文件读 10 遍 | +0.10% | [−0.11%, +0.26%] |
| `long` | 无法命中短路径的全长记录，文件读 10 遍 | +0.05% | [−0.12%, +0.15%] |
| `long_alias` | 同一个全长文件，参数路径多 22 个 `/`，文件读 10 遍 | +0.09% | [−0.13%, +0.28%] |

结果文件包括 `trials.csv`（各次墙钟及 CPU 纳秒值）、`summary.json`（配对耗时变化、重采样得到的 95% 置信区间和输出哈希）、`reproduction.json`（实际绑核、参数长度、候选布局审计等）。耗时变化为负表示更快，区间跨 0 表示该次测量无法区分小幅变快或变慢，区间全在 0 以上表示该次测量观察到回退。

表中是报告的原生 AMD 数据。三组压力测试的区间跨 0，结论限于该次原生实验。新增容器复测中，`long_alias` 曾出现区间全在 0 以上的小幅回退。具体数字、复测条件及验证范围见 [交付核验记录](docs/verification.md)。报告中的原始记录位于镜像 `/course/reference/`，可以通过 `bash reproduce.sh extract extracted` 取出，另存于 `extracted/reference/`。

## 补丁、回归测试与重新打包

从仓库目录设置 `course=$PWD`。准备干净的 `llvm-project` 工作树后进入该目录。构建需 CMake、Ninja 和 C/C++ 编译器。

```bash
git switch --detach cbfac21cd384f6c7c480c2d0026ecedfe2d619e7
git apply --check "$course/BOLT-short-memcpy-layout.patch"
git apply "$course/BOLT-short-memcpy-layout.patch"
cmake -S llvm -B build-bolt -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS_RELEASE='-O1 -DNDEBUG' \
  -DLLVM_ENABLE_ASSERTIONS=ON -DLLVM_ENABLE_PROJECTS=bolt \
  -DLLVM_TARGETS_TO_BUILD=X86 -DLLVM_INCLUDE_TESTS=OFF \
  -DLLVM_PARALLEL_LINK_JOBS=2
cmake --build build-bolt --target llvm-bolt --parallel 8
```

运行独立回归测试还需 Linux x86-64、Python 3、GCC、glibc 开发文件、GNU binutils。把当前目录切回课程仓库后，可以对 Release 自带的 BOLT 执行：

```bash
bash reproduce.sh extract docker/payload
python3 tests/check.py --bolt docker/payload/bin/llvm-bolt --utils docker/payload
```

验证自行编译的版本时，将 `--bolt` 和 `--utils` 分别改为 `llvm-project/build-bolt/bin/llvm-bolt` 和 `llvm-project/bolt/utils` 的实际路径。测试现场编译小型 ELF，覆盖 0–256 字节的页边界读取、写入范围、返回指针、16/17 字节分流、profile 阈值、长度被修改或窄比较时跳过，以及 ELF 哈希不符时的回退。布局空间不足的回退由 `smoke` 单独验证。

从已提取的 `docker/payload/` 重新打包。该目录含 `bin/llvm-bolt`、`inputs/mawk.orig`、`words.fdata` 和 `mawk.length-profile`、四组负载所用的输入及 AWK 脚本、`reproduce.py`、`bench.py`、布局审计脚本、原生实验记录和许可证。Dockerfile 从固定摘要的 Ubuntu 24.04 基础镜像安装运行依赖，构建时需要联网。要打包自行编译的 BOLT，可在提取后替换 `docker/payload/bin/llvm-bolt`，再运行 smoke 验证。重新打包的镜像 ID 和归档哈希可能变化，交付校验以 Release v1.0 为准。

```bash
docker build --platform linux/amd64 -t bolt-course:local docker
docker run --rm --network none --read-only --tmpfs /tmp:rw,exec bolt-course:local smoke
```
