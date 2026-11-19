# 第三方软件

电子书工作台的《求是》采集功能参考了 Qiushi-Downloader 的站点选择器和分享图片识别规则。原项目的 MIT 许可和版权声明随本仓库提供。[Qiushi-Downloader](https://github.com/XHSecurity/Qiushi-Downloader) · [MIT 许可原文](licenses/Qiushi-Downloader.txt)

源码运行时通过 Python 安装 Qt for Python、网页提取和 PDF 等依赖。编辑器由锁定的 npm 依赖构建。Windows 下载包在同一目录提供随附组件的版权声明、许可证和相应源码获取方式。[Python 依赖](requirements.txt) · [编辑器依赖](web/package-lock.json)

CSS 检查使用 tinycss2 和 webencodings。字体处理使用 fontTools，WOFF2 解码和编码使用 Brotli。相应许可证随 Windows 下载包提供。 [tinycss2](https://github.com/Kozea/tinycss2) · [webencodings](https://github.com/gsnedders/python-webencodings) · [fontTools](https://github.com/fonttools/fonttools) · [Brotli](https://github.com/google/brotli)

抓取和导入的作品版权归其权利人，软件依赖的许可证适用于对应的软件组件。

英文拼写检查使用 Spylls 0.1.7 和 English Speller Database 的美式英语词典（2026.02.25）。词典的 aff、dic 与上游发行包一致。Spylls 遵循 MPL 2.0，其完整源码可从发行页的源码包取得。 [Spylls 0.1.7 源码](https://pypi.org/project/spylls/0.1.7/#files) · [MPL 2.0 原文](licenses/Spylls-0.1.7.txt) · [英文词典发行页](https://github.com/en-wl/wordlist/releases/tag/rel-2026.02.25) · [词典版权和许可原文](licenses/SCOWL-English.txt)
