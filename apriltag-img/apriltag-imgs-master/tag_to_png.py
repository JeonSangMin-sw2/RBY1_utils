#!/usr/bin/env python3

import os
import sys
import argparse
import re
from PIL import Image

# Thanks to https://stackoverflow.com/a/54547257
def dir_path(file_path):
    if os.path.isfile(file_path):
        return file_path
    else:
        raise argparse.ArgumentTypeError(f'Supplied argument "{file_path}" is not a valid file path.')

parser = argparse.ArgumentParser(
    description='A script to convert pre-generated apriltag .png files into SVG or high-res PNG format.',
    epilog='Example: "python tag_converter.py tagStandard52h13/tag52_13_00007.png tag52_13_00007.svg --size=20mm"'
)
parser.add_argument(
    'tag_file', type=dir_path, 
    help='The path to the apriltag png you want to convert.'
)
parser.add_argument(
    'out_file', type=str, 
    help='The path to the output file. Extension determines format (.svg or .png).'
)
parser.add_argument(
    '--size', type=str, required=False, default='20mm', dest="output_size", 
    help='The size of the output. For SVG: "20mm", "2in". For PNG: assumes 300 DPI for physical units or use "500px".'
)

def parse_size_to_pixels(size_str, dpi=300):
    """
    Parses a size string (e.g., '20mm', '2in', '100px') and converts it to integer pixels.
    Defaults to 300 DPI for physical units.
    """
    # 숫자와 단위를 분리
    match = re.match(r"([0-9.]+)([a-zA-Z]+)", size_str)
    if not match:
        # 단위가 없으면 픽셀로 간주하거나 에러 처리 (여기서는 픽셀로 간주)
        try:
            return int(float(size_str))
        except ValueError:
            raise ValueError(f"Invalid size format: {size_str}")

    value, unit = float(match.group(1)), match.group(2).lower()

    if unit == 'px':
        return int(value)
    elif unit == 'in':
        return int(value * dpi)
    elif unit == 'mm':
        return int((value / 25.4) * dpi)
    elif unit == 'cm':
        return int((value / 2.54) * dpi)
    else:
        raise ValueError(f"Unsupported unit: {unit}. Use mm, cm, in, or px.")

def gen_apriltag_svg(width, height, pixel_array, size):
    def gen_rgba(rbga):
        (_r, _g, _b, _raw_a) = rbga
        _a = _raw_a / 255
        return f'rgba({_r}, {_g}, {_b}, {_a})'

    def gen_gridsquare(row_num, col_num, pixel):
        _rgba = gen_rgba(pixel)
        _id = f'box{row_num}-{col_num}'
        return f'\t<rect width="1" height="1" x="{row_num}" y="{col_num}" fill="{_rgba}" id="{_id}"/>\n'

    svg_text = '<?xml version="1.0" standalone="yes"?>\n'
    svg_text += f'<svg width="{size}" height="{size}" viewBox="0,0,{width},{height}" xmlns="http://www.w3.org/2000/svg">\n'
    for _y in range(height):
        for _x in range(width):
            svg_text += gen_gridsquare(_x, _y, pixel_array[_x, _y])
    svg_text += '</svg>\n'

    return svg_text

def gen_apriltag_png(im, size_str):
    """
    Resizes the PIL image using Nearest Neighbor interpolation.
    """
    target_px = parse_size_to_pixels(size_str)
    
    # NEAREST 필터를 사용해야 픽셀이 흐려지지 않고 각진 형태를 유지함 (AprilTag 인식 필수)
    resized_im = im.resize((target_px, target_px), resample=Image.NEAREST)
    return resized_im

def main():
    args = parser.parse_args()
    tag_file = args.tag_file
    out_file = args.out_file
    output_size = args.output_size

    with Image.open(tag_file, 'r') as im:
        width, height = im.size
        
        # 확장자 파싱
        _, ext = os.path.splitext(out_file)
        ext = ext.lower()

        if ext == '.svg':
            pix_vals = im.load()
            output_data = gen_apriltag_svg(width, height, pix_vals, output_size)
            with open(out_file, 'w') as fp:
                fp.write(output_data)
            print(f'Generated SVG: {out_file} (Size attribute: {output_size})')

        elif ext == '.png':
            # PNG 생성 로직
            resized_im = gen_apriltag_png(im, output_size)
            resized_im.save(out_file)
            print(f'Generated PNG: {out_file} (Size: {resized_im.size[0]}x{resized_im.size[1]} px)')

        else:
            print(f"Error: Unsupported output extension '{ext}'. Please use .svg or .png")
            sys.exit(1)

if __name__ == "__main__":
    main()
