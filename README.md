# ASC → 交互式曲线（asc-dbc-viewer）

把 Vector **ASC / BLF** 报文日志配合 **DBC** 解码成**单个离线 HTML**：
左侧信号树、右侧多曲线（各自独立 Y 轴）、悬停十字线读数、滚轮缩放、拖动平移、导出 CSV。
不联网、不用装 CANoe，双击 HTML 就能看。

- 底层：`python-can`（读帧）+ `cantools`（按 DBC 解码）+ 纯原生 Canvas 前端（无 CDN、无 plotly）
- 同一份 DBC 支持**单个或多个** ASC：可批量各出一份，也可 `--merge` 合成一张**对比图**
- 附带图形界面，并可打包成**免安装单文件 exe**（内嵌 Python，目标机零依赖）

## 特性

| 能力 | 说明 |
| --- | --- |
| 日志格式 | `.asc`（文本）、`.blf`（二进制） |
| 帧类型 | Classic CAN、CAN FD（含 64 字节）、扩展帧（29 bit）、多通道 |
| 多日志 | 批量出图 / `--merge` 同信号叠加对比（各自时间从 0 开始） |
| DBC | 多路复用（mux）、别名信号、信号超出报文长度（自动修） |
| 容错 | ASC 字段顺序变体、`base hex/dec`、分段日志时间戳重置（自动累加） |
| 界面 | 信号搜索、多选、独立 Y 轴、十字光标、区间导出 CSV |

## 安装

```bat
python -m pip install -r requirements.txt
```

只用源码运行时需要 Python 3.10+；不想装 Python 就用[打包版](#打包成免安装-exe)。

## 快速开始

```bat
:: 单个日志（同目录有 .dbc 会自动配对）
python asc2html.py 日志.asc
python asc2html.py 日志.asc 矩阵.dbc -o out.html --open

:: 多个日志：批量（每份各出一个 html）
python asc2html.py run1.asc run2.asc 矩阵.dbc -o 输出目录

:: 多个日志：对比（合成一张图，同信号叠加）
python asc2html.py run1.asc run2.asc 矩阵.dbc --merge -o 对比.html
```

没有现成日志时，可以先生成一份演示数据：

```bat
python examples\make_demo.py
python asc2html.py examples\run1.asc examples\run2.asc examples\demo.dbc --merge -o demo.html
```

### 图形界面

```bat
python gui.py          :: 或双击 asc2html.bat 以外的入口
```

ASC 支持 Ctrl/Shift 多选，勾选“多个日志合并为一张图”即为对比模式。

## 对比图（`--merge`）规则

- 每份日志的时间轴**各自从 0 开始**，总时长取最长的一份（比较波形形状，不比较绝对时刻）
- 顶部图例 = 文件名 ↔ 颜色；左侧信号名前的小色块表示该信号命中了哪几份日志
- 悬停读数按文件分行显示；导出 CSV 的列名形如 `M1.Speed[km/h]@run1`
- 多文件时 `-o` 是**输出目录**；想合成一张图必须加 `--merge`（给 `.html` 会明确报错）

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `-o, --out PATH` | 输出 HTML；多个 ASC 时视为输出目录 |
| `--dbc FILE` | 显式指定矩阵（也可把 `.dbc` 直接写进文件列表） |
| `--merge` | 多份日志合成一张对比图 |
| `--ids 10C,32B` | 只处理指定报文 ID（十六进制） |
| `--signal MotSpd` | 只输出匹配正则的信号（匹配 `报文名.信号名`） |
| `--from 100 --to 200` | 只取重整后时间轴的某一段（秒） |
| `--max-points 6000` | 降采样桶大小（保留每桶极值，实际点数约 1~2 倍） |
| `--max-signals 8 --bytes 8` | 无 DBC 时最多画几个 ID、每个 ID 取几个字节 |
| `--no-dbc` | 忽略 DBC（含自动发现），直接看原始字节 |
| `--no-rebase` | 不修正时间戳重置 |
| `--open` | 生成后打开浏览器 |

## 前端操作

滚轮缩放（以鼠标为中心）· 拖动平移 · 双击或 `0` 复位 · 左右方向键平移 ·
悬停出竖线与读数 · “导出 CSV”按当前可视区间均匀采 5000 点（采样保持）· 左侧搜索框过滤。

## 已处理的坑

1. **ASC 字段顺序变体**：标准 `CANFD <ch> <dir> <id>`，台架常写成 `CANFD <ch> <id> <dir>`；
   有的把 `d` 标记放在 dlc 前面。`python-can` 直接读会抛
   `ValueError: invalid literal for int() with base 16: 'R'`，本工具会先重排成它认识的格式。
2. **时间戳重置**：分段录制的日志每段从 0 开始，直接画会来回跳；默认累加成单调时间轴，
   并在标题栏显示重置次数（注意累计后总时长 ≠ 真实墙钟时长）。
3. **DBC 信号重叠**：同一段比特被定义多份（别名）会让 `cantools` 报
   `DecodeError: unpacking failed`；工具保留第一份、把同位置别名按原值回填，丢弃部分重叠的畸形信号。
4. **信号超出报文长度**：DBC 声明 64 字节却定义到 519 bit 之类；工具按最长信号扩一点长度，
   数据不足的位自动剔除，其余信号照常解码。
5. **首帧丢失**：`python-can` 读文件头时会吃掉第一条数据行；工具用合成文件头规避。

## 目录结构

```
asc2html.py        核心：解析 / 解码 / 生成 HTML（CLI 入口）
gui.py             tkinter 图形界面
app.py             打包入口：有参数走 CLI，无参数起 GUI
template.html      前端模板（Canvas 曲线 + 交互，单文件无外链）
tests/run_tests.py 15 个回归用例（无 pytest 依赖）
examples/          演示数据生成脚本
build.ps1          一键：测试 → PyInstaller → 免安装 zip
asc2html.bat       源码版拖拽入口
```

## 打包成免安装 exe

```bat
powershell -ExecutionPolicy Bypass -File build.ps1
```

产出 `release\ASC交互曲线_v<版本>_免安装.zip`（约 14 MB），内含单文件 exe + 使用说明 +
requirements。exe 已内嵌 Python 3.14、python-can、cantools、tkinter，目标机无需安装任何东西。

## 测试

```bat
python tests\run_tests.py
```

覆盖：3 种 FD 字段变体、`base hex/dec`、首帧、时间戳重置、无 DBC、mux、
别名/超长矩阵、CLI 过滤、payload 结构、BLF、多文件批量、`--merge`、错误处理、GUI 加载。

## 兼容性边界

- 支持 `.asc`（文本）/ `.blf`；Classic CAN、CAN FD、扩展帧、多通道、`base hex/dec`、mux。
- 跳过 ErrorFrame / 远程帧 / J1939 TP / statistic 等非数据行。
- 不支持：压缩或加密日志、只写符号名不写 ID 的日志；ARXML 取决于 `cantools` 的支持程度。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。
