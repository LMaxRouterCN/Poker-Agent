# Agent 指令列表

用法：在 【cmd】和【/cmd】标签之间编写指令，多条指令按顺序执行。

---

## 系统指令

### @@help
显示本帮助文档。
示例：
【cmd】@@help【/cmd】

---

## 精确内容操作

### count <文件路径>
统计文件的行数、字数（中英文混合精确统计）和字符数。
示例：
【cmd】count main.py【/cmd】

### find <文件路径> [选项]
精确查找文件内的文本，返回所有匹配的行号及内容。
选项：
- -i ：忽略大小写
- -w ：全词匹配（仅英文）
如果是多行查找，内容需要换行并用标签包裹。
示例：
【cmd】find test.txt -i -w 
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
**用两个独立的代码块分别提供旧文本和新文本**，第一个代码块是旧文本，第二个是新文本。
示例：
【cmd】replace config.json -a
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

注意replace指令不支持多行分别查找替换, 指令会把代码块内的所有内容作为一个整体

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
【cmd】insert main.py -before "def main():" 
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
【cmd】grep "TODO|FIXME|HACK" src【/cmd】
【cmd】grep "import|from" main.py -s【/cmd】


### head <文件路径> [行数]
查看文件头部内容，默认前 10 行。
示例：
【cmd】head log.txt 20【/cmd】

### tail <文件路径> [行数]
查看文件尾部内容，默认后 10 行。
示例：
【cmd】tail log.txt 50【/cmd】

---

## 文件操作

### create
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


- 文件路径：相对于工作目录，或使用绝对路径。
- 如果文件已存在，会覆盖原文件。
- 如果内容只有单行且不含特殊字符，也可以简写为：

【cmd】create <文件路径> <单行内容>【/cmd】

### read <文件路径> [起始行]-[结束行]
读取文件内容并返回。支持指定行号范围，指定范围时会自动附带行号。
如果处于剪贴板读取模式一次对话最多获取10个文件
- 不传行号：返回完整内容，如果当前程序处于纯文本模式超过 5000 字符会截断，如果处于剪贴板读取模式会使用API上传整个文件不受字数限制。
- `read <路径> 10-20`：读取第 10 到 20 行。
- `read <路径> 10-` 或 `read <路径> 10`：从第 10 行读到文件末尾。

示例：
【cmd】read notes.txt【/cmd】
【cmd】read main.py 10-20【/cmd】
【cmd】read main.py 50-【/cmd】


### append
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

### delete <文件路径>
删除指定文件。此操作不可逆。
示例：
【cmd】delete temp.txt【/cmd】

### copy <源路径> <目标路径>
复制文件。
示例：
【cmd】copy notes.txt backup.txt【/cmd】

### move <源路径> <目标路径>
移动或重命名文件。
示例：
【cmd】move old.txt new.txt【/cmd】

---

## 目录操作

### list <目录路径>
列出目录下的文件和子目录。不传路径时列出当前工作目录。
示例：
【cmd】list【/cmd】
【cmd】list src【/cmd】

注意:此指令不会递归,不会列出子目录的子目录和文件,如果要递归列出所有内容请使用【cmd】exec dir /s /b【/cmd】

### mkdir <目录路径>
创建目录（支持多级创建）。
示例：
【cmd】mkdir src/modules【/cmd】

---

## 系统命令

### exec <系统命令>
执行系统命令，返回输出。
示例：
【cmd】exec python --version【/cmd】
【cmd】exec dir【/cmd】

### run <脚本路径>
运行 Python 脚本，返回输出。
示例：
【cmd】run script.py【/cmd】

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
- 写入多行内容时，必须在外层使用 【CodeSTART】 和 【/CodeEND】 包裹，内侧用 ``` 包裹代码使其变为代码块。
- 替换整个文件内容用create覆写,而不是replace
- 如果要写入的内容本身包含三个反引号，请用 TICK3 代替避免在浏览器处理文本后打乱排版格式（后端会自动还原）。
- 代码块内的一切内容都会原封不动写入文件，包括空行、空格、特殊符号。
- replace 指令中，请用**两个独立的代码块**分别提供旧文本和新文本（按出现顺序区分）。
- 危险操作（delete、exec）会记录日志。
- 如果此文档中指令说明更新的不及时，你可以读取此项目下根目录的`agent_server.py`源文件以确定某个指令在代码中的实现方式。
- 【CodeSTART】 和 【/CodeEND】的作用就是标记代码块的起始和结束,所以在指令中每个代码块都必须用【CodeSTART】 和 【/CodeEND】包裹,【CodeSTART】 和 【/CodeEND】必须和代码块同时存在,如果没有代码块就不要用【CodeSTART】 和 【/CodeEND】
- 如果你不知道要修改的文件的内容,就不要操作文件,不要猜测,先read要修改的文件
- 任何情况下,指令内用于包裹代码的```的同一行都不能出现任何标识代码块的编程语言标签
不要写:
【cmd】hello.txt 
【CodeSTART】
```java
something
```
【/CodeEND】
【/cmd】

写:
【cmd】hello.txt 
【CodeSTART】
```
something
```
【/CodeEND】
【/cmd】
