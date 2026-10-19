"""Draw a QR inside Feishu's native chart; no image host or app token needed."""
from PIL import Image
import zxingcpp


def qr_chart(login_url):
    barcode = zxingcpp.create_barcode(login_url, zxingcpp.BarcodeFormat.QRCode)
    image = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, scale=1))
    size = image.width
    # Numeric field names also address compact [x,y] records in VChart.
    # Compact coordinates keep even long login URLs within card size limits.
    points = [[column, size - row - 1] for row in range(size) for column in range(size)
              if image.getpixel((column,row)) == 0]
    width = 260
    return {
        "tag": "chart", "aspect_ratio": "1:1", "height": f"{width}px", "preview": True,
        "chart_spec": {
            "type": "scatter", "width": width, "height": width, "autoFit": False,
            "region": [{"id": "qr", "width": width, "height": width}],
            "padding": 0, "background": "#ffffff", "animation": False, "media": [],
            "data": {"values": points}, "xField": "0", "yField": "1",
            "axes": [{"type": "linear", "orient": "bottom", "visible": False, "min": -.5, "max": size - .5, "nice": False},
                     {"type": "linear", "orient": "left", "visible": False, "min": -.5, "max": size - .5, "nice": False}],
            "point": {"style": {"symbolType": "square", "size": width / size + .15, "fill": "#000000", "stroke": False}},
            "tooltip": {"visible": False}, "legends": {"visible": False},
        },
    }
