#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日期转换工具
支持多种日期格式之间的相互转换
"""

import sys
import argparse
from datetime import datetime, timedelta
import re


def parse_date(date_str):
    """
    解析各种格式的日期字符串
    支持格式：
    - YYYY-MM-DD
    - YYYY/MM/DD
    - YYYY.MM.DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - YYYYMMDD
    - 中文格式：2024年1月1日
    - 相对日期：today, yesterday, tomorrow, +1d, -7d, +1m, -1y等
    """
    date_str = date_str.strip()
    
    # 处理相对日期
    if date_str.lower() == 'today':
        return datetime.now().date()
    elif date_str.lower() == 'yesterday':
        return (datetime.now() - timedelta(days=1)).date()
    elif date_str.lower() == 'tomorrow':
        return (datetime.now() + timedelta(days=1)).date()
    
    # 处理相对偏移
    relative_match = re.match(r'^([+-])(\d+)([dmwy])$', date_str)
    if relative_match:
        sign, num, unit = relative_match.groups()
        num = int(num)
        if sign == '-':
            num = -num
        
        base_date = datetime.now().date()
        if unit == 'd':
            return base_date + timedelta(days=num)
        elif unit == 'm':
            # 简单的月份处理（不考虑月份天数差异）
            month = base_date.month + num
            year = base_date.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            try:
                return base_date.replace(year=year, month=month)
            except ValueError:
                # 处理月末日期（如1月31日 + 1个月）
                from calendar import monthrange
                days_in_month = monthrange(year, month)[1]
                day = min(base_date.day, days_in_month)
                return base_date.replace(year=year, month=month, day=day)
        elif unit == 'y':
            try:
                return base_date.replace(year=base_date.year + num)
            except ValueError:
                # 处理2月29日等特殊情况
                return base_date.replace(year=base_date.year + num, day=28)
    
    # 处理各种标准格式
    formats = [
        '%Y-%m-%d',      # 2024-01-01
        '%Y/%m/%d',       # 2024/01/01
        '%Y.%m.%d',       # 2024.01.01
        '%d/%m/%Y',       # 01/01/2024
        '%d-%m-%Y',       # 01-01-2024
        '%Y%m%d',         # 20240101
        '%Y年%m月%d日',    # 2024年01月01日
        '%Y年%m月%d号',    # 2024年01月01号
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.date()
        except ValueError:
            continue
    
    raise ValueError(f"无法解析日期格式: {date_str}")


def format_date(date_obj, format_type="iso"):
    """
    格式化日期对象
    """
    if format_type == "iso":
        return date_obj.strftime('%Y-%m-%d')
    elif format_type == "china":
        return date_obj.strftime('%Y年%m月%d日')
    elif format_type == "us":
        return date_obj.strftime('%m/%d/%Y')
    elif format_type == "eu":
        return date_obj.strftime('%d/%m/%Y')
    elif format_type == "slash":
        return date_obj.strftime('%Y/%m/%d')
    elif format_type == "dot":
        return date_obj.strftime('%Y.%m.%d')
    elif format_type == "compact":
        return date_obj.strftime('%Y%m%d')
    elif format_type == "weekday":
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        return f"{date_obj.strftime('%Y年%m月%d日')} {weekdays[date_obj.weekday()]}
    else:
        return date_obj.strftime(format_type)


def main():
    parser = argparse.ArgumentParser(description='日期转换工具')
    parser.add_argument('date', help='输入日期（支持多种格式）')
    parser.add_argument('-f', '--format', default='iso', 
                       choices=['iso', 'china', 'us', 'eu', 'slash', 'dot', 'compact', 'weekday'],
                       help='输出格式（默认: iso）')
    parser.add_argument('--list-formats', action='store_true', 
                       help='列出所有支持的格式')
    
    args = parser.parse_args()
    
    if args.list_formats:
        print("支持的输入格式：")
        print("  - YYYY-MM-DD (2024-01-01)")
        print("  - YYYY/MM/DD (2024/01/01)")
        print("  - YYYY.MM.DD (2024.01.01)")
        print("  - DD/MM/YYYY (01/01/2024)")
        print("  - DD-MM-YYYY (01-01-2024)")
        print("  - YYYYMMDD (20240101)")
        print("  - 中文格式：2024年1月1日")
        print("  - 相对日期：today, yesterday, tomorrow")
        print("  - 相对偏移：+1d, -7d, +1m, -1y")
        print("\n支持的输出格式：")
        print("  - iso: YYYY-MM-DD")
        print("  - china: YYYY年MM月DD日")
        print("  - us: MM/DD/YYYY")
        print("  - eu: DD/MM/YYYY")
        print("  - slash: YYYY/MM/DD")
        print("  - dot: YYYY.MM.DD")
        print("  - compact: YYYYMMDD")
        print("  - weekday: YYYY年MM月DD日 星期X")
        return
    
    try:
        date_obj = parse_date(args.date)
        result = format_date(date_obj, args.format)
        print(result)
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()