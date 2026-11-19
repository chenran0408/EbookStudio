# 电子书工作台 Agent 指南

适用于通过本机 MCP 让 Agent 采集网页、识别扫描件、制作或编辑书稿、转换格式和校验 EPUB。MCP 服务器名为 `ebook-studio`，工具名以 `ebook_` 开头。将 Windows x64 ZIP 整包解压，保留 EXE 旁的 `_internal` 目录。使用本机 STDIO 注册 `EbookStudio-Agent.exe` 的绝对路径，启动参数设为 `mcp`。 [GitHub 发布页](https://github.com/chenran0408/EbookStudio/releases)

服务会在 MCP 初始化时提供简要操作说明。需要查看完整指南或示例时，调用无需参数的 `ebook_guide`，它会直接返回本指南的 Markdown。客户端接收到的指南不需要读取本机项目文件。

## 开始前

- 用 ebook_status 传入空对象，查看 actions、output_formats，以及各本机工具的 tools.<名称>.ready 和 message。按任务需要确认相应 OCR 或转换工具可用，遇到缺失时把具体原因告诉用户。
- OCR 模型和可选工具按需单独配置。可在 Windows 用户环境变量或 MCP 服务的 `env` 中指定路径。转换器使用 `EBOOK_STUDIO_PANDOC` 与 `EBOOK_STUDIO_CALIBRE`。Paddle 使用 `EBOOK_STUDIO_PADDLE_PYTHON` 与 `EBOOK_STUDIO_PADDLE_MODELS`。小米可用 `EBOOK_STUDIO_OCR_MODEL` 指定已安装的 Ollama 模型标签。
- 用绝对路径指定输入和输出，输出目录应已存在。把每个新文件保存到明确的 output 路径。保留输入文件。只在用户明确要求覆盖某个已有输出时传 overwrite: true，输入路径仍应与输出路径不同。
- .bookproj 用于继续编辑。EPUB 用于在阅读器中阅读。需要交付两者时，先保存可编辑工程，再从工程导出 EPUB。

## 工具与参数

| MCP 工具 | 必填参数 | 常用可选参数 |
| --- | --- | --- |
| ebook_guide | 无 | 返回本指南。 |
| ebook_status | 无 | 无 |
| ebook_inspect | source | chapter 从 1 开始。读取章节时可加 include_html 和 max_chars，字符上限默认 12000，最多 50000。 |
| ebook_create | chapters、output | title、author、language、overwrite。 |
| ebook_update | source、output，并至少提供一项修改 | metadata、replace_chapters、append_chapters、overwrite。 |
| ebook_discover | url | rendered。用于目录页，只列出候选链接。 |
| ebook_capture | urls、output | rendered、metadata、overwrite。urls 按成书顺序排列。 |
| ebook_ocr | source、output | engine 为 xiaomi 或 paddle，默认 xiaomi。pages 如 1,3-5，省略表示全部页面。还可传 overwrite。 |
| ebook_export | source、output | format、mobile、overwrite。format 默认从输出扩展名推断，epub 对应 EPUB 3，epub2 对应 EPUB 2 并仍使用 .epub。mobile 默认 true，优化可重排书籍中的 JPEG 和 PNG 正文图。封面和固定版式书籍保留原图。 |
| ebook_convert | source、output | format、overwrite。适用于已有文件的直接格式转换。 |
| ebook_check | source | 无。source 指向导出的 .epub。 |

metadata 支持 title、author、language。新章节包含 title 和 html，可加 level 和 source。html 填章节正文片段，level 从 1 开始，source 可记录原始网页 URL。replace_chapters 的每项含 chapter 编号和需要修改的字段。ebook_inspect、ebook_update 和 ebook_export 的 source 可使用 .bookproj 或 .epub。

## 按任务执行

### 扫描件制书

1. 常规单栏正文选 xiaomi（Xiaomi-OCR-0）。多栏杂志、有插图或跨行跨列表格选 paddle（PaddleOCR-VL-1.6）。用户指定模型时使用指定值。检查 ebook_status 中对应引擎的就绪状态。首次配置后先试一页。
2. 用 ebook_ocr 传原始 PDF 或图片的绝对路径、页码范围和 .bookproj 输出路径。例如识别 PDF 的 1 到 4 页时传以下参数，并将路径换成实际文件。

~~~json
{
  "source": "C:/Books/扫描件.pdf",
  "engine": "paddle",
  "pages": "1-4",
  "output": "C:/Books/扫描稿.bookproj"
}
~~~

3. 用 ebook_inspect 检查工程的章节数和顺序。抽查正文、插图和表格等关键内容时，传对应 chapter，必要时加 include_html: true。需要订正时用 ebook_update 保存新工程，再导出。

### 网页制书

1. 用户提供目录页时，先用 ebook_discover 取得候选链接，按用户指定范围和顺序选出文章。已给出文章 URL 时直接进入采集。
2. 用 ebook_capture 保存 .bookproj。网页需 JavaScript 才能加载正文时使用 rendered: true，所用电脑需要安装随包 Playwright 对应的 Chromium。查看返回的 warnings，核对缺页、缺图和章节顺序。
3. 用 ebook_inspect 查看工程。需整理目录时可用 ebook_update 修改章节的 title 和 level，保持用户选定的阅读顺序。

### 从头制作或编辑已有书

1. 新书用 ebook_create 提交按顺序排列的章节。已有电子书或工程先用 ebook_inspect 查书名、目录和章节编号。
2. 用 ebook_update 写出新的 .bookproj。只提交用户要求修改的元数据或正文。编辑有插图、链接或表格的章节时，先用 include_html: true 取得正文片段，并留意 html_truncated，保留需要的标记。
3. 需要分组目录时，以 level: 1 表示一级标题，以 level: 2 表示其下的章节。合订本可用一级期号配二级文章。目录的展开和折叠显示由阅读器决定。

## 导出、核验与交付

1. 用 ebook_export 从最终 .bookproj 导出到用户指定的 .epub。交付其他格式时按需选择 ebook_export 或 ebook_convert。
2. 对导出的 EPUB 再调用 ebook_inspect。与工程核对目录项的数量、标题、顺序和 level，并抽查关键章节。用户要求细查目录时，进一步核对 EPUB 导航条目的链接能到达对应章节或锚点。
3. 用 ebook_check 校验该 EPUB。确认工具返回成功且 valid 为 true 后，才报告 EPUBCheck 通过。若调用出错，给出报告里的具体问题、涉及文件或位置。若工具缺失，说明本次校验尚未完成。确保 `java` 可从 PATH 找到，将 `EBOOK_STUDIO_EPUBCHECK` 指向解压后的 `epubcheck.jar` 绝对路径。也可把解压的 `epubcheck-<版本>` 目录放在 `%LOCALAPPDATA%\EbookStudio\tools` 中。 [EPUBCheck 发布页](https://github.com/w3c/epubcheck/releases)
4. 把实际生成的工程和 EPUB 路径、目录核验结果与 EPUBCheck 结果发给用户。另列未成功处理的页面、文章或其他影响阅读的警告。

MCP 工具报错时查看 isError 和错误内容。也可以在 Windows 命令行执行 `EbookStudio-Agent.exe schema` 查看字段，执行 `EbookStudio-Agent.exe run <任务.json>` 读取 UTF-8 JSON。命令行任务在工具参数上增加 action 字段，例如 ebook_ocr 对应 `"action": "ocr"`。
