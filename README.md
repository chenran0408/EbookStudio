# 电子书工作台

在 Windows 上采集网页、写书、修改 EPUB 和批量转换格式。书籍工程使用 .bookproj，章节和图片一起保存，随时可以继续编辑。

## 下载和启动

1. 从 GitHub 发布页下载 Windows x64 ZIP，并把整个压缩包解压到一个文件夹。适用于 Windows 10 或 11。 [GitHub 发布页](https://github.com/chenran0408/EbookStudio/releases)
2. 双击 `EbookStudio-OCR.exe`。`EbookStudio-Agent.exe` 是可选的命令行与 MCP 入口。两个程序共用同目录里的 `_internal` 文件夹。
3. 新建书稿，写好正文后保存为 `.bookproj`，再导出 EPUB 3。写书、编辑可重排 EPUB、采集普通网页和导出 EPUB 3 可以直接使用。格式转换、EPUBCheck、需要浏览器渲染的网页以及本地 OCR 按需配置。ZIP 中未包含 OCR 模型。

首次启动时，如果系统提示缺少 `VCRUNTIME140.dll` 或 `MSVCP140.dll`，请安装 Microsoft Visual C++ v14 x64 运行库后再打开工作台。 [微软官方下载](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)

## 用示例制作第一本书

1. 在开始页点「用示例制作第一本书」，打开《窗边的影子》。点左边的章节可切换正文，第一章有一幅离线插图。
2. 在正文中改一句话，点「保存工程」，选一个位置保存 `.bookproj`。下次打开这个文件即可接着改。再次从首页打开示例会得到一份新的书稿。
3. 点「导出」，选 EPUB 3 和保存位置。在手机或平板阅读器里打开 `.epub`，用目录跳到各章。制作这本示例无需安装 OCR 或格式转换器。

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

通过「工具 → Agent 接入说明」查看 Codex、WorkBuddy 和 JSON 命令行的用法。连接同目录的 `EbookStudio-Agent.exe` 后，可以让 Agent 直接调用 `ebook_guide` 取得随包指南，不需要提供指南路径。

例如，可以说“用我的杂志署名校对规则预览这本书，给我看每步会改几处；确认后另存工程，导出 EPUB 并校验”。Agent 用 `ebook_rule_groups` 读取桌面保存的组，用 `ebook_proofread` 先预览再另存；新旧 EPUB 可用 `ebook_compare` 核对。桌面和 Agent 通过同一个本机工作区共享规则组。升级后，在客户端重新连接并检查这三个工具是否可见。[Agent 接入说明](Agent接入.md) · [工具及参数](agent.md)

## 找一句正文、制作书内目录和校对用词

**跨书找正文。** 打开「文件 → 跨书全文检索」，点击「添加书籍」一次选入多本 EPUB 或 `.bookproj`。输入记得的一句话，按 Enter。结果显示书名、期号、章节和命中片段，选中一行可查看原文件位置，双击可打开对应章节。同一期多篇文章共用一个章节文件时，若目录链接指向文内标题，工作台会打开预览并滚动到该标题。在「文件 → 书库」中也可以点「搜索正文」进入此窗口。年度合订本可从分期目录识别期号，缺少分期标记时显示「未知期号」。正文索引保存在本机，原书修改并保存后，可点「刷新索引」更新。

**可翻阅的书内目录。** 打开书稿，从「出书 → 生成或更新书内目录页」生成一页可随正文翻阅的目录，它沿用阅读器目录的层级和小节链接。需要调整收录内容时，先在「出书 → 目录和章节管理」修改，再执行一次「生成或更新书内目录页」。确认更新会替换工作台生成的这页内容，操作前会建立检查点。若导入的 EPUB 已将阅读器目录列入正文，直接在「目录和章节管理」修改即可。固定版式书籍也可从这里编辑阅读器目录，原有分页会保留。

**拼写与个人词典。** 打开「校对 → 拼写检查与个人词典」。工作台使用随包的美式英语词典检查英文，中文则检查你登记过的错词。在「个人词典」中填入错词和建议写法，例如「己经」和「已经」。若要认可人名、术语等专名，可将建议写法留空，或直接在检查结果中点「认可此词」。选中命中项可查看上下文、打开对应章节，或点「预览替换」核对整书匹配项，确认应用时会保存检查点。个人词典保存在本机，重启或更换书稿后仍然有效，可随时更新或移除词条。

## OCR 批次和书库

- **扫描件断点续做**。从「扫描件 OCR」选择原件、页码和已就绪模型。每页完成后会立即保存识别结果。在批次窗口可暂停，待本页结束后关闭。以后从开始页的「OCR 批次任务」打开同一批次，选择「继续识别」处理待识别页，或选择「重试失败页」「重试所选页」。原件移动时用「定位原件」选择内容一致的文件。「打开书稿」按原页序打开当时已识别的页面，使用「保存工程」保存为 `.bookproj`。
- **把书拖进书库**。在「文件 → 书库」将 EPUB 或 `.bookproj` 拖到窗口里，也可点击「批量添加书籍」。重复加入会更新已登记的资料。把多本书拖到主窗口时，可选择「加入书库」「批量转换」或「取消」。混合格式会显示每种操作能处理多少个文件，展开详细信息可逐个核对。
- **按作者和标签找书**。在「我的书稿」或「文件」菜单打开「书库」。可以搜索书名或路径，结合作者和标签筛选。选中一本可编辑书库资料，多选可批量加减标签。书库资料保存在本机，源文件保留原内容和位置。文件移动后可选中记录并「重新定位」。

## CSS 与字体排版

1. 从「出书 → CSS 与字体」打开排版窗口。选择样式表或新建全书样式，选择章节，再切换手机或平板预览。光标可定位到报告中的 CSS 问题。
2. 在「嵌入字体」中导入 TTF、OTF、WOFF 或 WOFF2，绑定到所选样式表，再查看字体试样。使用具有相应嵌入或修改许可的字体，并按字体许可随成品提供需要的声明。
3. 在「测算并启用导出子集」中核对预计大小后启用。编辑工程保留完整字体，导出时按书稿的用字重新计算字体子集，适合缩小带中文字体的 EPUB。应用排版前会记录检查点。

## 校对、目录与网页更新

- **只改需要校对的文字**。打开「校对 → 整书查找替换」或按 Ctrl+Shift+H，输入查找词和替换词，点「预览替换」。初始会勾选全部可替换项。只想改几处时，先点「全不选」，再勾选具体命中或整章；章名前的复选框控制该章所有页的命中。可用「上一页」「下一页」继续核对。点「应用已选 N 处」前，请确认数量符合预期。应用前会记录检查点。匹配限于单个正文文本节点，支持普通文字、正则表达式和捕获组。
- **复用校对规则**。点「保存为规则…」给常用改法起名。换一本书后，在「我的规则」中载入，再预览并选择本次要改的位置。同名保存可更新规则，也可删除已选规则。
- **按顺序运行多条规则**。打开书稿后，选「校对 → 顺序校对规则组」。点「添加步骤」填写查找文字和替换文字，也可以带入已有规则。例如 OCR 将原图中的紧排全角斜杠「／」识别成「 / 」，可先将半角斜杠改为全角，再去掉斜杠两旁多余的空格。用「上移」「下移」调整顺序，点「预览整组」，逐步核对命中数和前后文字，再点「应用整组」。整批修改前会保存一个检查点。常用组合可点「保存规则组」命名，下次打开其他书稿时也能选用。
  规则表里的 `␠` 表示一个首尾半角空格。带入已有规则会复制当时的设置。以后要改组内用词，在组里选中该步，点「编辑步骤」，再保存规则组。
- **把校对规则交给同事**。在「顺序校对规则组」点「导出规则组」，保存一份 JSON 文件。接收方打开任意书稿，进入同一窗口点「导入规则组」，即可在本机书稿上预览。遇到同名组，可以改名另存，也可以确认替换。
- **核对两份 EPUB**。从「校对 → 比较两份 EPUB」选择原版和修订版，点「开始比较」。左边看原版，右边看修订版；在变化列表中切换章节、目录或资源。可以直接从开始页使用，也可以在编辑中打开，关闭比较后继续原来的书稿。
  差异较多时按 Ctrl+F，输入章节名、书内路径或改动行里的文字。也可选「正文」或「资源」等类别缩小范围，点「重置筛选」回到全部变化。检查点的左右对照窗口也支持同样的操作。
- **比较和恢复检查点**。打开「校对 → 检查点」，选择一个版本，点「与当前书稿比较」。左侧是检查点，右侧是当前书稿，包含尚未保存的编辑。选择不同条目查看正文、章节顺序、目录或资源的变化；只改排版时可查看 XHTML 源码。点「返回检查点」可继续编辑或决定是否恢复。「恢复所选版本」会先保存当前版本，恢复后的书稿可以另存。
- **OCR 原图对照**。选中章节，打开「校对 → OCR 原图对照」。识别时自动记住文件和页码，旧书稿或拆分、合并后的章节可以手动关联。原件移动后可重新选择相同文件。重识别时选模型并核对新旧正文，只在采用结果后更新该章。
- **目录与资源**。在「出书 → 目录和章节管理」中选中一个条目，点击「编辑条目」可修改目录标题和章节或标题跳转位置。用「上移」「下移」「升一级」「降一级」调整顺序和层级。也可从正文标题生成目录、按标题拆章、合并相邻章节。核对预览后点击「应用更改」，工作台会记录修改前检查点。「书稿资源」可找出图片、样式和字体的引用，预览图片或 CSS，并替换支持的同格式资源。
- **网页增量采集**。网页采集时勾选保存规则和文章选择。以后从「出书 → 网页增量采集」检查更新。可以填写正文和目录 XPath，例如 `//article` 或 `//*[@id='contents']`。新增、变更和读取失败的项目会分别标出。已有章节默认保留，核对差异后可勾选更新。

生成阅读器目录时，在「出书 → 目录和章节管理」点「按标题生成目录」。例如目录里只想列章节和小节，可以先取消较低级别，再逐项取消图表标题。点「预览目录」核对层级，最后点「应用更改」。章节名始终保留，未选标题也仍在正文中。

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
