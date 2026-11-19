# 电子书工作台

在 Windows 上采集网页、写书、修改 EPUB 和批量转换格式。书籍工程使用 .bookproj，章节和图片一起保存，随时可以继续编辑。

## 下载和启动

1. 从 GitHub 发布页下载 Windows x64 ZIP，并把整个压缩包解压到一个文件夹。适用于 Windows 10 或 11。 [GitHub 发布页](https://github.com/chenran0408/EbookStudio/releases)
2. 双击 `EbookStudio-OCR.exe`。`EbookStudio-Agent.exe` 是可选的命令行与 MCP 入口。两个程序共用同目录里的 `_internal` 文件夹。
3. 新建书稿，写好正文后保存为 `.bookproj`，再导出 EPUB 3。写书、编辑可重排 EPUB、采集普通网页和导出 EPUB 3 可以直接使用。格式转换、EPUBCheck、需要浏览器渲染的网页以及本地 OCR 按需配置。ZIP 中未包含 OCR 模型。

首次启动时，如果系统提示缺少 `VCRUNTIME140.dll` 或 `MSVCP140.dll`，请安装 Microsoft Visual C++ v14 x64 运行库后再打开工作台。 [微软官方下载](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)

## 使用

1. 打开 EbookStudio-OCR.exe。正文和预览需要同目录的 _internal 文件夹。
2. 从开始页选择「新建书稿」「打开书籍或工程」「网页采集」或「扫描件 OCR」。「我的书稿」可以按文件名或路径搜索。选中常用书稿并点击「置顶」，就能在「只看置顶」中找到。用左侧「开始」与「书稿」切换界面，当前章节和未保存内容会留在书稿中。工作区的「导入内容」可向当前书稿追加章节。
3. 网页采集的「文章网址」支持每行一个链接。「从目录选文章」先查找页面里的文章，再勾选章节。《求是》期号支持专门的目录识别。
4. 扫描 PDF 或照片时，选择状态为「已就绪」的模型。小米适合正文和简单表格，Paddle 适合复杂分栏和合并单元格。PDF 或多页 TIFF 可以输入页码范围，例如 1,3-5。留空会识别全部页面。
5. 在左侧管理章节顺序和层级。按 Ctrl+P 查找章节标题，Enter 跳到第一项，Esc 清除搜索并恢复目录折叠。按 Ctrl+Shift+F 或点击「搜索正文」可在整书中查找文字，从结果打开章节。在「书籍资料」中改书名、作者、语言或封面。
6. 通过「正文」「源码」「预览」三个标签切换。在正文中开启「实时预览」，可边改边看手机或平板的版面。选中文字后按 Ctrl+K 可编辑链接，光标放进表格时会显示行列和单元格工具。特殊排版的章节可以使用 XHTML 源码编辑。
7. 点击「保存工程」或按 Ctrl+S，把章节和配图存为可继续编辑的 .bookproj。工作台会定期保存本机草稿。异常退出后，可在开始页恢复草稿，再另存为工程。
8. 导出前点「检查书稿」，查看缺图、断链、目录层级和空章节，可直接打开需要处理的章节。点击「导出」选择格式和文件夹。使用「另存为新名称」会保留已有同名文件。完成后可点「打开输出位置」。已配置 EPUBCheck 时，点击「导出校验报告」查看错误数、警告数和书内位置，也可复制报告。导出可重排 EPUB 时，可以为手机和平板压缩 JPEG 和 PNG 正文图。封面和固定版式书籍保留原图。取消勾选可保留原图画质。
9. 只想转换文件时，选择「批量转换格式」。Markdown 的配图随同级图片文件夹一起保存。「工具 → 检查本机工具」可以查看模型、转换器和 EPUBCheck 的安装状态。

找不到某个功能时，点击顶部「查找命令」或按 Ctrl+Shift+P。输入「目录」「OCR」「检查 EPUB」等关键词，用方向键和 Enter 执行，按 Esc 返回。Ctrl+P 用于跳转章节。

最近记录与草稿默认保存在 %LOCALAPPDATA%\EbookStudio。可通过 EBOOK_STUDIO_WORKSPACE 指定其他本机目录。

通过「工具 → Agent 接入说明」查看 Codex、WorkBuddy 和 JSON 命令行的用法。Agent 打包入口为同目录的 EbookStudio-Agent.exe。

## 校对、目录与网页更新

- **整书查找替换**。在「校对」菜单中打开，或按 Ctrl+Shift+H。输入查找和替换内容，核对命中章节与前后文字再应用。匹配在单个正文文本节点内。支持普通文字、正则表达式和捕获组。操作前会创建检查点。
- **检查点**。在「校对 → 检查点」中手动记录书稿版本或恢复。恢复前会保存当前版本，恢复出的工程可再保存到选定的位置。
- **OCR 原图对照**。选中章节，打开「校对 → OCR 原图对照」。识别时自动记住文件和页码，旧书稿或拆分、合并后的章节可以手动关联。原件移动后可重新选择相同文件。重识别时选模型并核对新旧正文，只在采用结果后更新该章。
- **目录与资源**。在「出书 → 目录和章节管理」中选中一个条目，点击「编辑条目」可修改目录标题和章节或标题跳转位置。用「上移」「下移」「升一级」「降一级」调整顺序和层级。也可从正文标题生成目录、按标题拆章、合并相邻章节。核对预览后点击「应用更改」，工作台会记录修改前检查点。「书稿资源」可找出图片、样式和字体的引用，预览图片或 CSS，并替换支持的同格式资源。
- **网页增量采集**。网页采集时勾选保存规则和文章选择。以后从「出书 → 网页增量采集」检查更新。可以填写正文和目录 XPath，例如 `//article` 或 `//*[@id='contents']`。新增、变更和读取失败的项目会分别标出。已有章节默认保留，核对差异后可勾选更新。

## 格式

| 来源 | 常用操作 |
|---|---|
| EPUB 2、EPUB 3 | 直接打开可重排 EPUB，编辑后导出 EPUB 3。可以通过 calibre 另存 EPUB 2。 |
| DOCX、ODT、RTF、Markdown、HTML、TXT | 转换为可编辑工程，或从书稿导出这些格式。 |
| AZW3、AZW、MOBI、KEPUB、FB2 | 导入后转换为章节。可导出 AZW3、MOBI、KEPUB、FB2。 |
| CBZ、CBR | 按图片页导入和重排，可导出 CBZ、PDF。直接转换为 EPUB 也可用。 |
| PDF、DjVu、CHM | 文本转换依赖原文件正文和阅读顺序。扫描 PDF 与图片可通过本地 OCR 转成书稿。 |

受 DRM 保护的书籍需要使用发行方提供的阅读方式。复杂 EPUB 章节可以用源码视图编辑。手机和平板预览提供页面宽度参考，最终分页和目录展开展现由阅读器决定。

## 按需安装工具

- **格式转换**。安装 Pandoc 和 calibre，根据选择的格式使用其中一项或两项。可在 Windows 用户环境变量中设置 `EBOOK_STUDIO_PANDOC` 和 `EBOOK_STUDIO_CALIBRE`，分别指向 `pandoc.exe` 和 `ebook-convert.exe` 的绝对路径。 [Pandoc 下载](https://pandoc.org/installing.html) · [calibre Windows 下载](https://calibre-ebook.com/download_windows)
- **EPUB 校验**。安装 Java，并确认新开的 PowerShell 能运行 `java -version`。完整解压 EPUBCheck，将 `EBOOK_STUDIO_EPUBCHECK` 指向其中 `epubcheck.jar` 的绝对路径。也可将版本目录放在 `%LOCALAPPDATA%\EbookStudio\tools` 下，例如 `epubcheck-5.4.0\epubcheck.jar`。导出时，工作台会检查生成的 EPUB 3，也可从「出书 → 检查 EPUB 文件」独立校验。 [Java 下载](https://adoptium.net/temurin/releases/) · [EPUBCheck 发布页](https://github.com/w3c/epubcheck/releases)

更改 Windows 用户环境变量后，重新打开工作台或 MCP 客户端。使用「工具 → 检查本机工具」确认所需的转换器、校验工具和模型已就绪。

### 网页需要浏览器渲染时

在解压后的工作台文件夹打开 PowerShell，运行下面的命令安装随包版本对应的 Chromium。安装完成后，在网页采集中勾选「网页内容加载不全时，使用浏览器渲染」。 [Playwright 浏览器说明](https://playwright.dev/python/docs/browsers)

~~~powershell
& '.\_internal\playwright\driver\node.exe' '.\_internal\playwright\driver\package\cli.js' install chromium --only-shell
~~~

### 本地 OCR

单栏正文和简单表格可选 **Xiaomi-OCR-0**。双栏杂志、插图和跨行跨列表格可选 **PaddleOCR-VL-1.6（复杂版面）**。下载模型之后，两种引擎都可以在本机离线识别。Paddle 会嵌入识别到的插图裁片，表格可在书稿中编辑。

**小米 OCR**。Windows 验收使用社区 Q8 模型，工作台默认查找 Ollama 标签 `xiaomi-ocr-0:q8_0`。首次安装可以按小米文档直接拉取社区 BF16 模型。安装并启动 Ollama 后，在工作台文件夹打开 PowerShell。 [Ollama Windows 安装](https://docs.ollama.com/windows) · [小米 Ollama 指南](https://github.com/xiaomi-research/Xiaomi-OCR-0/blob/main/OLLAMA.md) · [验收使用的 Q8 模型](https://github.com/xzl01/Xiaomi-OCR-0-GGUF/releases/tag/v1.0.0)

~~~powershell
ollama pull longwayxu/xiaomi-ocr-0:bf16
$env:EBOOK_STUDIO_OCR_MODEL = 'longwayxu/xiaomi-ocr-0:bf16'
.\EbookStudio-OCR.exe
~~~

使用其他模型目录时，可设置 `EBOOK_STUDIO_OCR_MODELS`。工作台也支持 Ollama 的 `OLLAMA_MODELS` 和默认的 `%USERPROFILE%\.ollama\models`。可用 `EBOOK_STUDIO_OLLAMA` 指定 `ollama.exe` 的绝对路径。使用 BF16 时，可将上面的 `EBOOK_STUDIO_OCR_MODEL` 保存为 Windows 用户环境变量，供双击启动和 MCP 使用。

**PaddleOCR-VL-1.6**。本版用 NVIDIA GPU `gpu:0`，需要兼容的显卡驱动及 GPU 运行时。安装 Windows x64 Python 3.12，创建独立环境。下面是验收用的 CUDA 12.9 组合。安装其他兼容 GPU 环境时，按 Paddle 文档选择对应的运行时。 [PaddleOCR-VL 安装指南](https://www.paddleocr.ai/v3.6.0/en/version3.x/pipeline_usage/PaddleOCR-VL.html)

~~~powershell
$paddle = Join-Path $env:LOCALAPPDATA 'EbookStudio\paddle312'
py -3.12 -m venv $paddle
$env:EBOOK_STUDIO_PADDLE_PYTHON = Join-Path $paddle 'Scripts\python.exe'
& $env:EBOOK_STUDIO_PADDLE_PYTHON -m pip install paddlepaddle-gpu==3.4.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
& $env:EBOOK_STUDIO_PADDLE_PYTHON -m pip install 'paddleocr[doc-parser]==3.6.0' paddlex==3.6.1
$env:EBOOK_STUDIO_PADDLE_MODELS = Join-Path $env:LOCALAPPDATA 'EbookStudio\models\paddle'
New-Item -ItemType Directory -Force -Path $env:EBOOK_STUDIO_PADDLE_MODELS
~~~

将下载的 `PP-DocLayoutV3` 和 `PaddleOCR-VL-1.6` 模型完整放到 `EBOOK_STUDIO_PADDLE_MODELS` 下，保留同名子目录。在同一 PowerShell 中运行 `.\EbookStudio-OCR.exe`，或保存这两个变量为 Windows 用户环境变量后重新打开程序。 [PP-DocLayoutV3 下载](https://modelscope.cn/models/PaddlePaddle/PP-DocLayoutV3) · [PaddleOCR-VL-1.6 下载](https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.6)

首次使用时，从「扫描件 OCR」打开一份本地 PDF，选择已就绪的模型并只识别第 `1` 页。核对识别文字和顺序后，再扩展到全部页面。

## 从源码运行

~~~powershell
python -m pip install -r requirements.txt
npm --prefix web ci
npm --prefix web run build
python studio.py
~~~

从源码启用浏览器渲染，运行 `python -m playwright install chromium --only-shell`。核心测试使用 `python -m unittest discover -s tests -v`。

如需构建 Windows x64 便携包，先按上述步骤安装依赖并构建编辑器，再运行以下命令。构建使用 Python 3.14 和 PyInstaller 6.22.3，生成的两个 EXE 位于 `dist/EbookStudio-OCR`。

~~~powershell
python -m pip install PyInstaller==6.22.3
python -m PyInstaller EbookStudio-OCR.spec
~~~

发布包的第三方组件和许可证说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

- [Tiptap](https://tiptap.dev/) 提供可视化编辑内核。
- [Pandoc](https://pandoc.org/) 处理稿件转换。
- [calibre](https://calibre-ebook.com/) 处理设备格式和 PDF 转换。
- [EPUBCheck](https://github.com/w3c/epubcheck) 检查 EPUB 标准符合性。
- [Xiaomi-OCR-0](https://github.com/xiaomi-research/Xiaomi-OCR-0) 识别扫描 PDF 和图片。
