#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日期格式转换工具
支持多种日期格式之间的相互转换
"""

import sys
import argparse
from datetime import datetime
import re


def parse_date(date_str, input_format=None):
    """
    解析日期字符串
    """
    # 常见的日期格式列表
    common_formats = [
        '%Y-%m-%d',           # 2024-01-15
        '%Y/%m/%d',           # 2024/01/15
        '%Y.%m.%d',           # 2024.01.15
        '%Y%m%d',             # 20240115
        '%d-%m-%Y',           # 15-01-2024
        '%d/%m/%Y',           # 15/01/2024
        '%d.%m.%Y',           # 15.01.2024
        '%Y-%m-%d %H:%M:%S',  # 2024-01-15 14:30:45
        '%Y/%m/%d %H:%M:%S',  # 2024/01/15 14:30:45
        '%Y-%m-%d %H:%M',     # 2024-01-15 14:30
        '%Y/%m/%d %H:%M',     # 2024/01/15 14:30
        '%m/%d/%Y',           # 01/15/2024
        '%m-%d-%Y',           # 01-15-2024
        '%B %d, %Y',          # January 15, 2024
        '%b %d, %Y',          # Jan 15, 2024
        '%d %B %Y',           # 15 January 2024
        '%d %b %Y',           # 15 Jan 2024
    ]
    
    if input_format:
        formats_to_try = [input_format]
    else:
        formats_to_try = common_formats
    
    for fmt in formats_to_try:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    
    # 如果标准格式失败，尝试正则匹配
    # 匹配 YYYY-MM-DD HH:MM:SS 格式
    match = re.match(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})(?:[\sT](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?', date_str.strip())
    if match:
        groups = match.groups()
        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        hour = int(groups[3]) if groups[3] else 0
        minute = int(groups[4]) if groups[4] else 0
        second = int(groups[5]) if groups[5] else 0
        return datetime(year, month, day, hour, minute, second)
    
    raise ValueError(f"无法解析日期字符串: {date_str}")


def format_date(dt, output_format):
    """
    格式化日期对象
    """
    try:
        return dt.strftime(output_format)
    except ValueError as e:
        raise ValueError(f"无效的输出格式: {output_format}. 错误: {e}")


def main():
    parser = argparse.ArgumentParser(description='日期格式转换工具')
    parser.add_argument('date', help='输入的日期字符串')
    parser.add_argument('-i', '--input-format', help='输入日期格式 (如: %%Y-%%m-%%d)')
    parser.add_argument('-o', '--output-format', required=True, 
                       help='输出日期格式 (如: %%Y年%%m月%%d日)')
    parser.add_argument('--list-formats', action='store_true', 
                       help='列出所有支持的日期格式')
    
    args = parser.parse_args()
    
    if args.list_formats:
        print("支持的日期格式说明:")
        print("%Y - 四位年份 (2024)")
        print("%y - 两位年份 (24)")
        print("%m - 月份 (01-12)")
        print("%B - 完整月份名 (January)")
        print("%b - 缩写月份名 (Jan)")
        print("%d - 日期 (01-31)")
        print("%A - 完整星期名 (Monday)")
        print("%a - 缩写星期名 (Mon)")
        print("%H - 小时 (00-23)")
        print("%I - 小时 (01-12)")
        print("%M - 分钟 (00-59)")
        print("%S - 秒 (00-59)")
        print("%p - AM/PM")
        print("\n示例:")
        print("  python date_converter.py '2024-01-15' -o '%Y年%m月%d日'")
        print("  python date_converter.py '15/01/2024' -i '%d/%m/%Y' -o '%Y-%m-%d'")
        return
    
    try:
        # 解析输入日期
        dt = parse_date(args.date, args.input_format)
        
        # 转换格式
        result = format_date(dt, args.output_format)
        
        print(result)
        
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()