# Agent 指令列表
用法：在 【cmd】和【/cmd】标签之间编写指令，多条指令按顺序执行。

---

## 系统指令
### @@help
显示本帮助文档。
示例：
【cmd】@@help【/cmd】

---

## 文件操作
### create
创建一个新文件并写入内容。文件路径写在指令同行，多行内容必须使用 【CodeSTART】 和 【/CodeEND】 标签包裹（内部仍需保留"```"以维持代码缩进）。
格式：
【cmd】
create <文件路径>
【CodeSTART】
```
<文件内容>
```
【/CodeEND】
【/cmd】
- 文件路径：相对于工作目录，或使用绝对路径。
- 如果文件已存在，会覆盖原文件。
- 如果内容只有单行且不含特殊字符，也可以简写为：
【cmd】create <文件路径> <单行内容>【/cmd】
示例：
【cmd】
create hello.txt
【CodeSTART】
```
Hello World!
这是第二行。
```
【/CodeEND】
【/cmd】
### read
<文件路径>
读取文件内容并返回。
示例：
【cmd】read notes.txt【/cmd】
### append
向已有文件末尾追加内容。多行格式与 create 相同。
格式：
【cmd】
append <文件路径>
【CodeSTART】
```
<追加内容>
```
【/CodeEND】
【/cmd】
简写：
【cmd】append <文件路径> <单行内容>【/cmd】
### delete
<文件路径>
删除指定文件。此操作不可逆。
示例：
【cmd】delete temp.txt【/cmd】
### copy
<源路径> <目标路径>
复制文件。
示例：
【cmd】copy notes.txt backup.txt【/cmd】
### move
<源路径> <目标路径>
移动或重命名文件。
示例：
【cmd】move old.txt new.txt【/cmd】

---

## 目录操作
### list
<目录路径>
列出目录下的文件和子目录。不传路径时列出当前工作目录。
示例：
【cmd】list【/cmd】
【cmd】list src【/cmd】
### mkdir
<目录路径>
创建目录（支持多级创建）。
示例：
【cmd】mkdir src/modules【/cmd】

---

## 系统命令
### exec
<系统命令>
执行系统命令，返回输出。
示例：
【cmd】exec python --version【/cmd】
【cmd】exec dir【/cmd】
### run
<脚本路径>
运行 Python 脚本，返回输出。
示例：
【cmd】run script.py【/cmd】

---

## 网络操作
### get
<URL>
发送 HTTP GET 请求，返回响应内容。
示例：
【cmd】get https://httpbin.org/get【/cmd】
### download
<URL> <保存路径>
下载文件到本地。
示例：
【cmd】download https://example.com/img.png images/img.png【/cmd】

---

## 注意事项
- 写入多行内容时，必须使用 【CodeSTART】 和 【/CodeEND】 包裹（内部保留"```"代码块以防止缩进丢失）。
- 代码块内的一切内容都会原封不动写入文件，包括空行、空格、特殊符号。
- 危险操作（delete、exec）会记录日志。
