"""An original, editable book opened from the start page."""
from pathlib import Path

from ebook_core import Asset, Book, Chapter


def first_book() -> Book:
    picture = Path(__file__).with_name("samples") / "pencil-shadows.png"
    return Book(
        title="窗边的影子",
        author="电子书工作台",
        chapters=[
            Chapter("一支铅笔的影子", (
                "<p>清晨，我在窗边的白纸上竖起一支铅笔。阳光越过窗框，"
                "把细长的影子放在了纸上。我沿着它的尖端，轻轻点下一个记号。</p>"
                "<p>到中午，铅笔还在原处，影子却缩短了。下午再看，"
                "它已经越过铅笔，伸向纸的另一边。</p>"
                '<p><img src="images/pencil-shadows.png" alt="铅笔影长示意：九时十二厘米，'
                '十二时五厘米，十五时九厘米，午后影子转向另一侧。"/></p>'
                "<p>图一　同一支铅笔在三个时刻的影子</p>"
            )),
            Chapter("三个记号", (
                "<p>九点的记号离笔尖最远。十二点的记号贴近铅笔底部，"
                "三点的记号又退开了一段。把时刻写在记号旁，白纸就成了一本小小的观察簿。</p>"
                "<h2>下次再来窗边</h2><p>我在纸角记下铅笔的位置。"
                "明天，把铅笔放回这里，看看同一时刻的影子是否会落在今天的记号上。</p>"
            ), level=2),
        ],
        assets={"images/pencil-shadows.png": Asset(picture.read_bytes(), "image/png")},
    )
