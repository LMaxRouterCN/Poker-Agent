# Agent 指令列表

---

## 指令快速预览列表和说明
#### @@help [参数]
智能帮助查询系统。根据不同的参数，提供不同粒度的帮助信息，方便快速查阅。
**无参数或 `all`**：
显示完整的 `commands.md` 帮助文档。内容较长，适合完整阅读。
**`fast`**：
显示“指令快速预览列表和说明”章节，快速了解所有可用指令的用途。适合想快速知道“有什么指令”的场景。
**`[指令名]`**：
显示指定指令的详细用法说明。适合想深入了解某个指令如何使用的场景。
示例：
【cmd】@@help【/cmd】
【cmd】@@help `all`【/cmd】
【cmd】@@help `fast`【/cmd】
【cmd】@@help `replace`【/cmd】
【cmd】@@help `exec`【/cmd】
#### start
作用：初始化，返回后端运行时环境和设置信息 - 输入信息：无参数 - 返回信息：工作目录、各项配置开关、PowerShell 版本、Python 版本、操作系统等
#### count
作用：统计文件的行数、字数和字符数 - 输入信息：文件路径 - 返回信息：行数、字数、字符数统计结果
#### find
作用：智能查找（根据是否有代码块自动切换两种模式） - 输入信息：文件或目录路径 [选项] 查找内容或文件名 - 返回信息：文件内容搜索返回匹配的行号及内容；文件名搜索返回匹配文件的完整绝对路径
#### deleteline
作用：删除文件内的某一行/几行，或删除匹配的文本 - 输入信息：文件路径 [选项 -l 行号/范围 或 -i/-w/-a 及文本内容] - 返回信息：操作结果
#### replace
作用：精确替换文件内的文本（支持内容匹配或行号模式）- 输入信息：文件路径 [选项] 旧文本和新文本（或行号模式下的新文本）- 返回信息：操作结果
#### insert
作用：在指定位置插入内容 - 输入信息：文件路径 -after/-before <行号或文本> 及插入内容 - 返回信息：操作结果
#### grep
作用：在文件或目录中按正则表达式搜索匹配的行 - 输入信息：[选项] "正则模式" 文件或目录路径 - 返回信息：匹配的行（格式 文件:行号:内容），支持 -i/-v/-c/-l/-w/-r/-s/-e/--include/--exclude
#### head
作用：查看文件头部内容 - 输入信息：文件路径 [行数，默认10] - 返回信息：文件头部指定行数的内容
#### tail
作用：查看文件尾部内容 - 输入信息：文件路径 [行数，默认10] - 返回信息：文件尾部指定行数的内容
#### create
作用：创建新文件并写入内容 - 输入信息：文件路径 [文件内容] - 返回信息：操作结果
#### read
作用：读取文件内容 - 输入信息：文件路径 [起始行]-[结束行] - 返回信息：文件内容（指定行号范围时附带行号）
#### append
作用：向已有文件末尾追加内容 - 输入信息：文件路径 [追加内容] - 返回信息：操作结果
#### delete
作用：删除指定文件或目录（移入专属回收站，支持恢复） - 输入信息：文件或目录路径 - 返回信息：操作结果
#### restore
作用：从专属回收站恢复文件或目录到原路径 - 输入信息：恢复模式（"最近" / 文件名 / --path 完整路径） - 返回信息：操作结果
#### copy
作用：复制文件 - 输入信息：源路径 目标路径 - 返回信息：操作结果
#### move
作用：移动或重命名文件 - 输入信息：源路径 目标路径 - 返回信息：操作结果
#### list
作用：列出目录下的文件和子目录（不递归） - 输入信息：目录路径（不传则列出当前工作目录） - 返回信息：文件和子目录列表
#### mkdir
作用：创建目录（支持多级创建） - 输入信息：目录路径 - 返回信息：操作结果
#### exec
作用：执行系统命令（终端可能是 PowerShell 或 CMD，需先确认，支持【CodeSTART】代码块格式执行多行命令） - 输入信息：系统命令（单行内联或代码块格式） - 返回信息：命令执行的输出
#### run
作用：运行 Python 脚本 - 输入信息：Python脚本路径 - 返回信息：脚本运行的输出
#### get
作用：发送 HTTP GET 请求 - 输入信息：URL - 返回信息：HTTP 响应内容
#### download
作用：下载文件到本地 - 输入信息：URL 保存路径 - 返回信息：操作结果

**如果不清楚用法请发送【cmd】@@help `[指令名]`【/cmd】获取用法,或者寻求管理员**

## 系统命令快捷参考（exec 指令）
请尽量使用专用指令,如果专用指令出现问题,或者无对应的专用指令,或者为了效率,可以使用 exec 指令.

**⚠ 以下示例全部基于 CMD 语法。** 如果当前终端是 PowerShell，大部分命令需要改写（参见 exec 指令说明中的语法差异对照）。使用前请先确认终端类型。

以下操作可以通过以下 exec 指令完成。
### 目录与文件浏览
列出当前目录：exec dir /b
列出指定目录：exec dir /b <路径>
递归列出所有文件：exec dir /s /b <路径>
按名称搜索文件：exec dir /s /b <路径>\*<关键词>*
查看文件大小和属性：exec dir <文件路径>
### 目录操作
创建目录（支持多级）：exec mkdir <路径>
同时创建多个目录：exec mkdir <路径1> <路径2>
### 文件操作
复制文件：exec copy <源路径> <目标路径>
移动/重命名文件：exec move <源路径> <目标路径>
删除文件：exec del <文件路径>
删除文件（不提示）：exec del /q <文件路径>
删除目录及其内容：exec rd /s /q <目录路径>
### 文件内容查看（简单场景）
查看整个文件：exec type <文件路径>
查看文件前N行：exec powershell "Get-Content <文件路径> -Head <N>"
查看文件后N行：exec powershell "Get-Content <文件路径> -Tail <N>"
### 环境信息
查看当前工作目录：exec cd
查看环境变量：exec set
查看PATH：exec echo %PATH%
查看当前日期时间：exec echo %date% %time%
查看磁盘空间：exec wmic logicaldisk get size,freespace,caption
查看系统信息：exec systeminfo
### 网络相关
测试连通性：exec ping <地址>
查看本机IP：exec ipconfig
查看端口占用：exec netstat -ano | findstr <端口号>
查看指定PID的进程：exec tasklist | findstr <PID>
### 进程管理
查看所有进程：exec tasklist
结束进程：exec taskkill /pid <PID> /f
按名称结束进程：exec taskkill /im <进程名> /f
### 其他实用
计算文件行数：exec find /c /v "" <文件路径>
按编码查看文件（如UTF-8）：exec powershell "Get-Content <文件路径> -Encoding UTF8"

## 注意事项
- **此文件是存在换行的,如果你在看到这个文件内容时没有换行证明有些信息在传输过程中丢失了,立刻停止所有动作并告知用户**
- 小改动时不要频繁用create指令,很慢而且很消耗上下文窗口,小改动时使用replace或insert指令,当改动大到替换整个文件内容时再用create覆写,而不是replace
- 代码块内的一切内容都会原封不动写入文件，包括空行、空格、特殊符号。
- replace 指令中，请用**两个独立的代码块**分别提供旧文本和新文本（按出现顺序区分）。
- 危险操作（delete、exec）会记录日志。
- 如果此文档中指令说明更新的不及时，你可以读取此项目下根目录的`agent_server.py`源文件以确定某个指令在代码中的实现方式。
- 如果你不知道要修改的文件的内容,就不要操作文件,不要猜测,先read要修改的文件


## PokerAgent独特的指令格式规定

在【cmd】和【/cmd】标签之间编写指令，多条指令按顺序执行。
- 一对cmd标签中可以有多行的单行指令,以换行分隔
- 一对cmd标签中只能有一个多行指令

1. 如果文件或目录路径中包含空格，必须使用双引号 "" 将路径包裹起来，例如 read `"11111.md"` `10-20`.不加引号时，空格会被视为参数分隔符.
2. 如果要写入的内容本身包含三个反引号，请用 TICK3 代替避免在浏览器处理文本后打乱排版格式（后端会自动还原）.
3. 【CodeSTART】和【/CodeEND】的作用就是标记代码块的起始和结束,所以在指令中每个markdown代码块都必须用【CodeSTART】和【/CodeEND】包裹,【CodeSTART】和【/CodeEND】必须和代码块同时存在,如果没有代码块就不要用【CodeSTART】和【/CodeEND】.
4. 指令中指令的传入参数需要用"`"包裹,在这边并没有严格的格式规定,可以是【cmd】read `"11111.md"` `10-20`【/cmd】分开包裹,也可以【cmd】read `"11111.md" 10-20`【/cmd】.

- 任何情况下,指令内用于包裹代码的```的同一行都不能出现任何标识代码块的编程语言标签
    >写:
    >【cmd】create `hello.txt`
    >【CodeSTART】
    >```
    >something
    >```
    >【/CodeEND】
    >【/cmd】
    >不要写:
    >【cmd】create `hello.txt`
    >【CodeSTART】
    >```java
    >something
    >```
    >【/CodeEND】
    >【/cmd】
- 关于```,TICK3,三联反引号的详细说明
    >
    >**正确示例:**
    >好的,我会帮你写一段说明文本,介绍使用三联反引号创建代码块的方式.
    >【cmd】create `code_blocks.md`
    >【CodeSTART】
    >```
    >Yes, you can use TICK3 to create code blocks, like this:
    >TICK3
    >something
    >TICK3
    >```
    >【/CodeEND】
    >【/cmd】
    >
    >**完全错误示例:**
    >好的,我会帮你写一段说明文本,介绍使用```创建代码块的方式.
    >【cmd】create `code_blocks.md`
    >【CodeSTART】
    >TICK3
    >Yes, you can use ``` to create code blocks, like this:
    >三联反引号
    >something
    >三联反引号
    >TICK3
    >【/CodeEND】
    >【/cmd】

---

# 已安装的CLI扩展程序 - 对应的起始指令

>此部分由用户编写.

1. OpenCLI - 【cmd】exec `opencli list`【/cmd】

---

# 紧急告示栏

*⚠️ 已知问题：`exec` 指令在某些情况下可能出错*

**现象**
在执行 `exec cd` 或其他简单系统命令时，返回类似错误：
```
'cd"' 不是内部或外部命令，也不是可运行的程序或批处理文件。
```
**注意**：错误信息中的命令末尾多了一个双引号（`"`）。

**影响范围**
- **Python 3.14.2**（以及可能的其他版本）在 Windows 系统上使用 `subprocess.run(cmd, shell=True)` 时，内部构造的命令字符串被错误地加上了额外的双引号。
- 该问题**不是 PokerAgent 的代码缺陷**，而是 Python 或 Windows 底层行为异常。
- **此问题仅在 CMD 终端模式下触发。** PowerShell 模式使用列表传参（不经过 shell=True），不受影响。频繁遇到此问题时建议将终端切换为 PowerShell。

目前建议**等待 Python 或 Microsoft 发布修复**,或尝试降级python,无其他可完美解决问题的方法

该问题仅涉及 `exec` 指令，其余文件操作、内容查找等指令均运行正常。

---

### @@help [参数]
智能帮助查询系统。根据不同的参数，提供不同粒度的帮助信息，方便快速查阅。
**无参数或 `all`**：
显示完整的 `commands.md` 帮助文档。内容较长，适合完整阅读。
**`fast`**：
显示“指令快速预览列表和说明”章节，快速了解所有可用指令的用途。适合想快速知道“有什么指令”的场景。
**`[指令名]`**：
显示指定指令的详细用法说明。适合想深入了解某个指令如何使用的场景。
示例：
【cmd】@@help【/cmd】
【cmd】@@help `all`【/cmd】
【cmd】@@help `fast`【/cmd】
【cmd】@@help `replace`【/cmd】
【cmd】@@help `exec`【/cmd】
### start
返回后端当前的运行时环境和设置信息。无需任何参数。建议在首次接入时调用一次以获取环境上下文。
返回内容包括：
- 当前工作目录（work_dir）
- 剪贴板读取模式（clipboard_mode）
- 系统命令执行开关（exec_enabled）
- 终端类型（shell_type：powershell 或 cmd）
- 目录权限限制开关（permission_enabled）
- 始终允许列表条目数（always_allow_count）
- 操作系统（platform）
- PowerShell 版本
- Python 版本
示例：
【cmd】start【/cmd】
### count <文件路径>
统计文件的行数、字数（中英文混合精确统计）和字符数。
示例：
【cmd】count `main.py`【/cmd】
【cmd】count `"my file.txt"`【/cmd】
### find <文件或目录路径> [选项] <查找内容或文件名>
智能查找指令，根据是否有代码块自动切换两种模式。
选项：
- -r ：启用正则表达式匹配
- -p ：部分匹配（搜索词是目标的子串或正则部分匹配）
- -i ：忽略大小写
无修饰参数时默认：全匹配 + 不忽略大小写。
**模式一：文件内容查找（使用【CodeSTART】标签时触发）**
路径必须是文件。支持单行或多行连续匹配。
示例 (全匹配单行)：
【cmd】find `main.py`
【CodeSTART】
```
def main():
```
【/CodeEND】
【/cmd】
示例 (正则部分匹配)：
【cmd】find `main.py` `-r` `-p`
【CodeSTART】
```
import .* from .*
```
【/CodeEND】
【/cmd】
**模式二：文件名递归搜索（不使用【CodeSTART】标签时触发）**
路径必须是目录。递归向下搜索匹配的文件名。
选项同上。若需使用通配符（如 *.txt），请开启 -r 使用正则（如 .*\.txt）。
示例 (全匹配文件名)：
【cmd】find `./` `main.py`【/cmd】
示例 (正则部分匹配查找日志文件)：
【cmd】find `"D:\projects\"` `-r` `-p` `.*\.log`【/cmd】
### deleteline <文件路径> [选项]
删除文件内的某一行或几行，或删除匹配的文本。
**行号模式**：使用 `-l` 选项指定行号或范围。
示例：
【cmd】deleteline `test.txt` `-l` `5`【/cmd】
【cmd】deleteline `"test file.txt"` `-l` `5-10`【/cmd】
**文本模式**：不使用 `-l` 选项，指定要删除的文本。
支持选项
- `-i`（忽略大小写）
- `-w`（全词匹配）
- `-a`（删除所有匹配）
示例：
【cmd】deleteline `test.txt` `-i` `-w` `hello`【/cmd】
【cmd】deleteline `test.txt` `-a`
【CodeSTART】
```
hello world
```
【/CodeEND】
【/cmd】
### replace <文件路径> [选项]
**选项：**
- `-a`：替换所有匹配项（默认只替换第一个）
- `-i`：忽略大小写
- `-l <行号范围>`：按行号替换，格式为 `-l 5`（单行）或 `-l 5-20`（范围），只需提供一个代码块（新文本）
- `-s`：忽略缩进。匹配时忽略每行首尾的空格、Tab 和缩进；替换时新内容会自动继承原文本块第一行的缩进
- `-w`：空白归一化。将连续的空白字符（包括缩进、内部多空格、Tab）统一视为单个空格进行匹配
- `-f`：模糊匹配。基于相似度匹配，默认阈值为 0.92
- `-f-0.X`：自定义模糊匹配阈值。例如 `-f-0.85` 表示相似度达到 85% 即视为匹配
**匹配模式说明：**
- 以上修饰参数（`-i`, `-s`, `-w`, `-f`）**可以任意组合**使用。例如 `-s -w -f-0.8` 会依次执行：剥离缩进、归一化空白、最后计算相似度。
- **取消自动降级**：如果不加任何修饰参数，就是纯精确匹配。加上某个参数后，严格按该模式匹配，找不到就报错并返回最接近的诊断信息，不会自动尝试其他模式。
**内容匹配模式**（默认）：用两个独立的代码块分别提供旧文本和新文本，第一个代码块是旧文本，第二个是新文本。
示例（组合匹配：忽略缩进 + 忽略大小写）：
【cmd】replace `config.json` `-s` `-i`
【CodeSTART】
```
"debug": false
```
【/CodeEND】
【CodeSTART】
```
"debug": true
```
【/CodeEND】
【/cmd】
**行号模式**（-l）：直接指定要替换的行范围，只提供新文本。
示例：
【cmd】replace `"config file.json"` `-l` `10-15`
【CodeSTART】
```
// 新的代码
```
【/CodeEND】
【/cmd】
注意replace指令*不支持*多行分别查找替换, 指令会把一个代码块内的所有内容作为一个整体
建议尽可能使用行号模式,因为内容匹配模式由于缩进和换行比较脆弱,而且原文本如果tab和空格混用这种情况下将极难排查
### insert <文件路径> -after/-before <行号或文本>
在指定位置插入内容。可以指定行号，也可以指定一段目标文本。
示例（在第10行后插入）：
【cmd】insert `main.py` `-after` `10`
【CodeSTART】
```
print("插入的内容")
```
【/CodeEND】
【/cmd】
示例（在目标文本前插入）：
【cmd】insert `"main file.py"` `-before` `"def main():"`
【CodeSTART】
```
# 这是新插入的注释
```
【/CodeEND】
【/cmd】
### grep [选项] "模式" <文件或目录路径>
在文件或目录中按正则表达式搜索匹配的行。模式默认作为 Python 正则表达式解析（re.search 语义）。
**选项：**
- `-i`：忽略大小写
- `-v`：反向匹配，输出不匹配的行
- `-c`：仅输出每个文件的匹配行数，不输出内容
- `-l`：仅输出包含匹配的文件名，不输出内容
- `-w`：全词匹配，自动在模式外包裹 \b 词边界
- `-r`：递归搜索目录（搜索目录时必须加此选项，否则报错）
- `-s`：匹配前去除行首空白（忽略缩进）
- `-e`：指定多个模式，可多次使用，任一匹配即命中。使用 -e 时路径为最后一个非选项参数
- `--include`：文件名过滤正则，仅搜索文件名匹配的文件（如 --include "\.py$"）
- `--exclude`：文件名排除正则，跳过文件名匹配的文件（如 --exclude "\.min\.js$"）
短选项可合并书写，如 -ivr 等价于 -i -v -r。
**输出格式：**
- 单文件：每行 `行号:内容`，首行为文件路径
- 目录递归：每行 `文件路径:行号:内容`
- -c 模式：`文件路径:匹配数`
- -l 模式：每行一个文件路径
示例：
【cmd】grep `"def\s+\w+"` `main.py`【/cmd】
【cmd】grep `-ir` `"todo|fixme"` `src`【/cmd】
【cmd】grep `-r` `--include` `"\.py$"` `"import\s+os"` `.`【/cmd】
【cmd】grep `-c` `"error"` `app.log`【/cmd】
【cmd】grep `-e` `"foo"` `-e` `"bar"` `config.txt`【/cmd】

---

### create <文件路径> [内容]
创建一个新文件并写入内容。文件路径写在指令同行，多行内容必须使用 【CodeSTART】 和 【/CodeEND】 标签包裹
格式：
【cmd】create <文件路径>
【CodeSTART】
```
<文件内容>
```
【/CodeEND】
【/cmd】
示例：
【cmd】create `hello.txt`
【CodeSTART】
```
Hello World!
这是第二行。
```
【/CodeEND】
【/cmd】
- 文件路径：相对于工作目录，或使用绝对路径。**如果路径包含空格，必须用双引号包裹**。
- 如果文件已存在，会覆盖原文件。
- 如果内容只有单行且不含特殊字符，也可以简写为：
【cmd】create <文件路径> <单行内容>【/cmd】
- 简写模式下，如果路径含空格，需用引号将路径包裹，剩余部分为内容：
【cmd】create `"hello world.txt"` `Hello`【/cmd】
### read <文件路径> [起始行]-[结束行]
读取文件内容并返回。支持指定行号范围，指定范围时回执会自动附带行号。
- 不传行号：返回完整内容。
- `read <路径> 10-20`：读取第 10 到 20 行。
- `read <路径> 10-` 或 `read <路径> 10`：从第 10 行读到文件末尾。
- **如果路径包含空格，必须用双引号包裹**。
示例：
【cmd】read `notes.txt`【/cmd】
【cmd】read `"11111.md"`【/cmd】
【cmd】read `"11111.md"` `10-20`【/cmd】
【cmd】read `main.py` `50-`【/cmd】
有的api/网页渠道会限制单次返回的字符数量,程序没有做保护,需要用户自己处理.
如果处于剪贴板读取模式会使用服务商提供的API上传整个文件,无感字数限制(但文本会被压缩,具体压缩措施取决于服务商,通常会失去其中所有的换行),单次对话最多获取的文件数量通常也会被限制,建议一次最多读取5个文件。
### append <文件路径> [内容]
向已有文件末尾追加内容。多行格式与 create 相同。
格式：
【cmd】append <文件路径>
【CodeSTART】
```
<追加内容>
```
【/CodeEND】
【/cmd】
简写：
【cmd】append <文件路径> <单行内容>【/cmd】
- 简写模式下，如果路径含空格，需用引号将路径包裹，剩余部分为内容：
【cmd】append `"my notes.txt"` `新增的一行`【/cmd】
### delete <文件或目录路径>
删除指定文件或目录。**此操作不会永久删除，而是将目标移动到工作目录下的专属回收站（`.agent_trash`）中，可以通过 `restore` 指令恢复。**
- 严格限制：指令格式必须为 `delete "路径"` 或 `delete 路径`，**不允许携带任何额外参数**（如 `-f` 等，否则直接拒绝）。`防止你手滑`
- 安全机制：拒绝删除工作目录本身；拒绝直接操作专属回收站。
- 目录支持：支持直接删除目录及其内部所有文件。
示例：
【cmd】delete `temp.txt`【/cmd】
【cmd】delete `"temp file.txt"`【/cmd】
【cmd】delete `src/modules`【/cmd】
### restore <恢复模式>
从专属回收站（`.agent_trash`）恢复被 `delete` 指令删除的文件或目录至其原始路径。
**支持三种恢复模式：**
1. **恢复最近删除**：使用关键字 `最近`。
   示例：
   【cmd】restore `最近`【/cmd】
2. **按原文件/目录名恢复**：直接提供被删除时的名称。如果回收站中存在同名但不同路径的历史记录，系统将拒绝直接恢复并列出冲突项，要求使用完整路径模式。
   示例：
   【cmd】restore `"config.json"`【/cmd】
3. **按完整路径精确恢复**：使用 `--path` 参数指定原始绝对路径（支持带斜杠的目录格式）。这是最精确的全自动恢复方式。
   示例：
   【cmd】restore `--path` `"D:\projects\src\utils.py"`【/cmd】
   【cmd】restore `--path` `"D:\projects\src\my_dir\"`【/cmd】
**注意事项：**
- 如果原路径当前已存在同名文件/目录，为防止覆盖，恢复操作将被中止。
- 恢复成功后，会自动清理回收站中对应的空目录层级。
### copy <源路径> <目标路径>
复制文件。
示例：
【cmd】copy `notes.txt` `backup.txt`【/cmd】
【cmd】copy `"my notes.txt"` `"my backup.txt"`【/cmd】
### move <源路径> <目标路径>
移动或重命名文件。
示例：
【cmd】move `old.txt` `new.txt`【/cmd】
【cmd】move `"old file.txt"` `"new file.txt"`【/cmd】

---

### list <目录路径>
列出目录下的文件和子目录。不传路径时列出当前工作目录。
示例：
【cmd】list【/cmd】
【cmd】list `src`【/cmd】
【cmd】list `"my src"`【/cmd】
注意:此指令不会递归,不会列出子目录的子目录和文件,如果要递归列出所有内容请使用【cmd】exec `dir` `/s` `/b`【/cmd】
### mkdir <目录路径>
创建目录（支持多级创建）。
示例：
【cmd】mkdir `src/modules`【/cmd】
【cmd】mkdir `"my modules/src"`【/cmd】

---

### exec <系统命令>
执行系统命令，返回输出。支持两种命令格式。
**命令格式（两种）：**
1. **单行内联**：`exec <命令>`。
2. **代码块格式**：exec 独占一行，命令内容用【CodeSTART】和【/CodeEND】代码块包裹。
代码块格式下，按提供的块**数量**区分两种行为：
- **单代码块**：整个块的内容作为一个多行脚本，在**同一个进程**中按顺序执行，块内各行可共享变量。
- **多代码块**：每个块成为**独立任务**，各自启动**独立进程**依次串行执行，块与块之间**无状态共享、不拼接**，且各自产生一条独立回执。单个块失败不影响后续块。
示例（单代码块：同一进程，共享变量）：
【cmd】exec
【CodeSTART】
```
$files = Get-ChildItem -Recurse -File
$files | Sort-Object Length -Descending | Select-Object -First 10 Name, Length
```
【/CodeEND】
【/cmd】
示例（多代码块：两个独立进程先后执行，第二个块拿不到第一个块的变量）：
【cmd】exec
【CodeSTART】
```
python --version
```
【/CodeEND】
【CodeSTART】
```
node --version
```
【/CodeEND】
【/cmd】
建议始终使用代码块格式,因为有的时候某些特殊字符会在单行模式下破坏内容
**如何选择单块还是多块：**
- 多行命令之间存在依赖（后一步要用到前一步的变量或结果）→ 必须写在**同一个块**内
- 多条互不相关的命令（如分别查看 python 和 node 版本）→ 用**多个块**，彼此隔离、失败互不拖累、回执各自独立
**代码块格式说明：**
- 内联文本与代码块同时存在时，以代码块为准，内联部分被忽略
- 代码块内容原样作为命令执行，**不做 TICK3 还原**（TICK3 规定仅适用于写入文件的内容），不要在 exec 代码块内使用 TICK3
- 多行命令仅在 PowerShell 终端下可靠执行，CMD 终端请改用单行命令
- 多代码块模式下：每个块独立进行危险命令确认（含危险关键词的块可能各弹一次确认窗），各块的超时限制独立计时
**危险命令确认**：命令中包含删除/格式化类关键词（del、rd、rm、ri、Remove-Item、format、erase、diskpart、mkfs、shred、Clear-Disk、Initialize-Disk、Remove-Partition）时，会先请求用户确认，被拒绝则不执行。
**终端类型：**
exec 使用的系统终端由后端配置决定，回退链为：
1. PowerShell 7+（pwsh）— 优先
2. Windows PowerShell 5.x（powershell）— 未安装 pwsh 时回退
3. 命令提示符（cmd）— 未检测到任何 PowerShell 时回退
**⚠ 你不确定当前终端是哪种。** 编写 exec 命令前必须先确认：
- 方法一：询问用户
- 方法二：执行 `exec $PSVersionTable.PSVersion.ToString()`，返回版本号则为 PowerShell，报错则为 CMD
**PowerShell 与 CMD 语法差异示例：**
- CMD: `dir /b` → PS: `Get-ChildItem -Name`
- CMD: `type file.txt` → PS: `Get-Content file.txt`
- CMD: `echo %PATH%` → PS: `$env:PATH`
- CMD: `findstr "xxx" file` → PS: `Select-String "xxx" file`
- CMD: `set` → PS: `Get-ChildItem Env:`
请根据实际终端类型编写对应语法的命令。
示例：
【cmd】exec `python` `--version`【/cmd】
【cmd】exec `$PSVersionTable.PSVersion.ToString()`【/cmd】
【cmd】exec `Get-ChildItem` `-Name`【/cmd】
解码格式会动态获取系统编码

### run <脚本路径>
运行 Python 脚本，返回输出。
示例：
【cmd】run `script.py`【/cmd】
【cmd】run `"my script.py"`【/cmd】

---

### get <URL>
发送 HTTP GET 请求，返回响应内容。
示例：
【cmd】get `https://httpbin.org/get`【/cmd】
### download <URL> <保存路径>
下载文件到本地。
示例：
【cmd】download `https://example.com/img.png` `images/img.png`【/cmd】
【cmd】download `https://example.com/img.png` `"my images/img.png"`【/cmd】

---