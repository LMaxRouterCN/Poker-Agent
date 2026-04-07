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
旧文本和新文本之间，**必须用单独一行的 【SepTag】 作为分隔符**。
示例：
【cmd】replace config.json -a 
【CodeSTART】
```
"debug": false
【SepTag】
"debug": true
```
【/CodeEND】
【/cmd】

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
示例：
【cmd】grep "TODO" src【/cmd】

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

### read <文件路径>
读取文件内容并返回。
示例：
【cmd】read notes.txt【/cmd】

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

## 注意事项
- 写入多行内容时，必须在外层使用 【CodeSTART】 和 【/CodeEND】 包裹，内测用 ``` 包裹代码使其变为代码块。
- 如果要写入的内容本身包含三个反引号，请用 TICK3 代替避免在浏览器处理文本后打乱排版格式（后端会自动还原）。
- 代码块内的一切内容都会原封不动写入文件，包括空行、空格、特殊符号。
- replace 指令中，新旧内容的分界线必须是**单独一行的 【SepTag】**。
- 危险操作（delete、exec）会记录日志。
