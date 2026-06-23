# Agent 指令列表
用法：在【cmd】和【/cmd】标签之间编写指令，多条指令按顺序执行。
一对cmd标签中可以有多行的单行指令,以换行分隔
一对cmd标签中只能有一个多行指令
## 通用规则
- **路径包含空格**：如果文件或目录路径中包含空格，必须使用双引号 `""` 将路径包裹起来，例如 `read "11111.md" 10-20`。不加引号时，空格会被视为参数分隔符。

---

## 指令快速预览列表和说明
### @@help
作用：显示本帮助文档 - 输入信息：无 - 返回信息：帮助文档内容
### count
作用：统计文件的行数、字数和字符数 - 输入信息：文件路径 - 返回信息：行数、字数、字符数统计结果
### find
作用：智能查找（根据路径类型自动切换文件内容搜索或文件名递归搜索） - 输入信息：文件或目录路径 [选项] 查找内容或文件名 - 返回信息：文件内容搜索返回匹配的行号及内容；文件名搜索返回匹配文件的完整绝对路径
### deleteline
作用：删除文件内的某一行/几行，或删除匹配的文本 - 输入信息：文件路径 [选项 -l 行号/范围 或 -i/-w/-a 及文本内容] - 返回信息：操作结果
### replace
作用：精确替换文件内的文本（支持内容匹配或行号模式） - 输入信息：文件路径 [选项] 旧文本和新文本（或行号模式下的新文本） - 返回信息：操作结果
### insert
作用：在指定位置插入内容 - 输入信息：文件路径 -after/-before <行号或文本> 及插入内容 - 返回信息：操作结果
### grep
作用：在文件或目录中搜索包含关键词的行 - 输入信息：关键词（支持|分隔） 文件或目录 - 返回信息：包含关键词的行内容（末尾提示命中的关键词）
### head
作用：查看文件头部内容 - 输入信息：文件路径 [行数，默认10] - 返回信息：文件头部指定行数的内容
### tail
作用：查看文件尾部内容 - 输入信息：文件路径 [行数，默认10] - 返回信息：文件尾部指定行数的内容
### create
作用：创建新文件并写入内容 - 输入信息：文件路径 [文件内容] - 返回信息：操作结果
### read
作用：读取文件内容 - 输入信息：文件路径 [起始行]-[结束行] - 返回信息：文件内容（指定行号范围时附带行号）
### append
作用：向已有文件末尾追加内容 - 输入信息：文件路径 [追加内容] - 返回信息：操作结果
### delete
作用：删除指定文件 - 输入信息：文件路径 - 返回信息：操作结果
### copy
作用：复制文件 - 输入信息：源路径 目标路径 - 返回信息：操作结果
### move
作用：移动或重命名文件 - 输入信息：源路径 目标路径 - 返回信息：操作结果
### list
作用：列出目录下的文件和子目录（不递归） - 输入信息：目录路径（不传则列出当前工作目录） - 返回信息：文件和子目录列表
### mkdir
作用：创建目录（支持多级创建） - 输入信息：目录路径 - 返回信息：操作结果
### exec
作用：执行系统命令 - 输入信息：系统命令 - 返回信息：命令执行的输出
### run
作用：运行 Python 脚本 - 输入信息：Python脚本路径 - 返回信息：脚本运行的输出
### get
作用：发送 HTTP GET 请求 - 输入信息：URL - 返回信息：HTTP 响应内容
### download
作用：下载文件到本地 - 输入信息：URL 保存路径 - 返回信息：操作结果

**如果不清楚用法请在`commands.md`中查询对应的指令获取用法,或者寻求管理员**

---

## 系统指令
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
【cmd】@@help all【/cmd】
【cmd】@@help fast【/cmd】
【cmd】@@help replace【/cmd】
【cmd】@@help exec【/cmd】

---

## 精确内容操作
### count <文件路径>
统计文件的行数、字数（中英文混合精确统计）和字符数。
示例：
【cmd】count main.py【/cmd】
【cmd】count "my file.txt"【/cmd】
### find <文件或目录路径> [选项] <查找内容或文件名>
智能查找指令，根据路径类型自动切换两种模式。
**模式一：文件内容搜索（路径指向文件时触发）**
精确查找文件内的文本，返回所有匹配的行号及内容。
选项：
- -i ：忽略大小写
- -w ：全词匹配（仅英文）
示例:
【cmd】find test.txt hello【/cmd】
【cmd】find "test file.txt" -i -w hello【/cmd】
单行简写时查找内容不允许有空格。
多行查找，内容需要换行并用标签包裹。
示例：
【cmd】find test.txt -i -w
【CodeSTART】
```
hello world
hello world1
```
【/CodeEND】
【/cmd】
**模式二：文件名递归搜索（路径指向目录时触发）**
递归向下搜索目录及其子目录，返回所有匹配文件的完整绝对路径。支持通配符 * 和 ?。
选项：
- -i ：忽略大小写（文件名匹配时有效）
- -w ：无效（自动忽略）
示例:
【cmd】find "D:\projects\" readme.md【/cmd】
【cmd】find "D:\projects\" -i *.txt【/cmd】
【cmd】find ./ *.log【/cmd】
### deleteline <文件路径> [选项]
删除文件内的某一行或几行，或删除匹配的文本。
**行号模式**：使用 `-l` 选项指定行号或范围。
示例：
【cmd】deleteline test.txt -l 5【/cmd】
【cmd】deleteline "test file.txt" -l 5-10【/cmd】
**文本模式**：不使用 `-l` 选项，指定要删除的文本。
支持选项
- `-i`（忽略大小写）
- `-w`（全词匹配）
- `-a`（删除所有匹配）
示例：
【cmd】deleteline test.txt -i -w hello【/cmd】
【cmd】deleteline test.txt -a
【CodeSTART】
```
hello world
```
【/CodeEND】
【/cmd】
### replace <文件路径> [选项]
精确替换文件内的文本。
选项：
- -a ：替换所有（默认只替换第一个）
- -i ：忽略大小写
- -s ：忽略缩进（按去除首尾空格后的内容匹配，替换时自动继承目标行的缩进）
- -l <行号范围> ：按行号替换，格式为 `-l 5`（单行）或 `-l 5-20`（范围），只需提供一个代码块（新文本）
**内容匹配模式**（默认）：用两个独立的代码块分别提供旧文本和新文本，第一个代码块是旧文本，第二个是新文本。
示例：
【cmd】replace config.json -a -s
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
【cmd】replace "config file.json" -l 10-15
【CodeSTART】
```
// 新的代码
```
【/CodeEND】
【/cmd】
注意replace指令*不支持*多行分别查找替换, 指令会把代码块内的所有内容作为一个整体
建议尽可能使用行号模式,因为内容匹配模式由于缩进和换行比较脆弱,而且原文本如果tab和空格混用这种情况下将极难排查
### insert <文件路径> -after/-before <行号或文本>
在指定位置插入内容。可以指定行号，也可以指定一段目标文本。
示例（在第10行后插入）：
【cmd】insert main.py -after 10
【CodeSTART】
```
print("插入的内容")
```
【/CodeEND】
【/cmd】
示例（在目标文本前插入）：
【cmd】insert "main file.py" -before "def main():"
【CodeSTART】
```
# 这是新插入的注释
```
【/CodeEND】
【/cmd】
### grep <关键词> <文件或目录>
在文件或整个目录中搜索包含关键词的行（类似 Linux grep）。
支持使用 `|` 分隔多个关键词进行 OR 搜索（任意一个命中即显示），命中行末尾会提示命中的关键词。
示例：
【cmd】grep "TODO" src【/cmd】
【cmd】grep "TODO|FIXME|HACK" "src code"【/cmd】
【cmd】grep "import|from" main.py -s【/cmd】
### head <文件路径> [行数]
查看文件头部内容，默认前 10 行。
示例：
【cmd】head log.txt 20【/cmd】
【cmd】head "my log.txt" 20【/cmd】
### tail <文件路径> [行数]
查看文件尾部内容，默认后 10 行。
示例：
【cmd】tail log.txt 50【/cmd】
【cmd】tail "my log.txt" 50【/cmd】
---
## 文件操作
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
【cmd】create hello.txt
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
【cmd】create "hello world.txt" Hello【/cmd】
### read <文件路径> [起始行]-[结束行]
读取文件内容并返回。支持指定行号范围，指定范围时会自动附带行号。
如果处于剪贴板读取模式一次对话最多获取10个文件
- 不传行号：返回完整内容，如果当前程序处于纯文本模式超过 5000 字符会截断，如果处于剪贴板读取模式会使用API上传整个文件不受字数限制。
- `read <路径> 10-20`：读取第 10 到 20 行。
- `read <路径> 10-` 或 `read <路径> 10`：从第 10 行读到文件末尾。
- **如果路径包含空格，必须用双引号包裹**。
示例：
【cmd】read notes.txt【/cmd】
【cmd】read "11111.md"【/cmd】
【cmd】read "11111.md" 10-20【/cmd】
【cmd】read main.py 50-【/cmd】
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
【cmd】append "my notes.txt" 新增的一行【/cmd】
### delete <文件路径>
删除指定文件。此操作不可逆。
示例：
【cmd】delete temp.txt【/cmd】
【cmd】delete "temp file.txt"【/cmd】
### copy <源路径> <目标路径>
复制文件。
示例：
【cmd】copy notes.txt backup.txt【/cmd】
【cmd】copy "my notes.txt" "my backup.txt"【/cmd】
### move <源路径> <目标路径>
移动或重命名文件。
示例：
【cmd】move old.txt new.txt【/cmd】
【cmd】move "old file.txt" "new file.txt"【/cmd】
---
## 目录操作
### list <目录路径>
列出目录下的文件和子目录。不传路径时列出当前工作目录。
示例：
【cmd】list【/cmd】
【cmd】list src【/cmd】
【cmd】list "my src"【/cmd】
注意:此指令不会递归,不会列出子目录的子目录和文件,如果要递归列出所有内容请使用【cmd】exec dir /s /b【/cmd】
### mkdir <目录路径>
创建目录（支持多级创建）。
示例：
【cmd】mkdir src/modules【/cmd】
【cmd】mkdir "my modules/src"【/cmd】
---
## 系统命令
### exec <系统命令>
执行系统命令，返回输出。
exec后的文本即是输入进cmd中的内容
示例：
【cmd】exec python --version【/cmd】
【cmd】exec dir【/cmd】
解码格式会动态获取系统编码
### run <脚本路径>
运行 Python 脚本，返回输出。
示例：
【cmd】run script.py【/cmd】
【cmd】run "my script.py"【/cmd】
---
## 网络操作
### get <URL>
发送 HTTP GET 请求，返回响应内容。
示例：
【cmd】get https://httpbin.org/get【/cmd】
### download <URL> <保存路径>
下载文件到本地。
示例：
【cmd】download https://example.com/img.png images/img.png【/cmd】
【cmd】download https://example.com/img.png "my images/img.png"【/cmd】
---
## 系统命令快捷参考（exec 指令）
请尽量使用专用指令,如果专用指令出现问题,或者无对应的专用指令,或者为了效率,可以使用 exec 指令.
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
- 小改动时不要频繁用create指令,很慢而且很消耗上下文窗口,小改动时使用replace或insert指令
- 写入多行内容时，必须在外层使用 【CodeSTART】 和 【/CodeEND】 包裹，内侧用 ``` 包裹代码使其变为代码块。
- 替换整个文件内容时用create覆写,而不是replace
- 如果要写入的内容本身包含三个反引号，请用 TICK3 代替避免在浏览器处理文本后打乱排版格式（后端会自动还原）。
- 代码块内的一切内容都会原封不动写入文件，包括空行、空格、特殊符号。
- replace 指令中，请用**两个独立的代码块**分别提供旧文本和新文本（按出现顺序区分）。
- 危险操作（delete、exec）会记录日志。
- 如果此文档中指令说明更新的不及时，你可以读取此项目下根目录的`agent_server.py`源文件以确定某个指令在代码中的实现方式。
- 【CodeSTART】 和 【/CodeEND】的作用就是标记代码块的起始和结束,所以在指令中每个代码块都必须用【CodeSTART】 和 【/CodeEND】包裹,【CodeSTART】 和 【/CodeEND】必须和代码块同时存在,如果没有代码块就不要用【CodeSTART】 和 【/CodeEND】
- 如果你不知道要修改的文件的内容,就不要操作文件,不要猜测,先read要修改的文件
- 任何情况下,指令内用于包裹代码的```的同一行都不能出现任何标识代码块的编程语言标签
    >写:
    >【cmd】create hello.txt
    >【CodeSTART】
    >```
    >something
    >```
    >【/CodeEND】
    >【/cmd】
    >不要写:
    >【cmd】create hello.txt
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
    >【cmd】create code_blocks.md
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
    >【cmd】create code_blocks.md
    >【CodeSTART】
    >TICK3
    >Yes, you can use ``` to create code blocks, like this:
    >三联反引号
    >something
    >三联反引号
    >TICK3
    >【/CodeEND】
    >【/cmd】



# 已安装的CLI扩展程序 - 对应的起始指令
1. OpenCLI - 【cmd】exec opencli list【/cmd】


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

目前建议**等待 Python 或 Microsoft 发布修复**,或尝试降级python,无其他可完美解决问题的方法

该问题仅涉及 `exec` 指令，其余文件操作、内容查找等指令均运行正常。
