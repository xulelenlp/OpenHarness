#!/usr/bin/env python3
# English case converter script

import sys
import argparse


def convert_case(text, mode):
    """
    Convert text case based on the specified mode
    
    Args:
        text (str): Input text to convert
        mode (str): Conversion mode - 'upper', 'lower', 'title', 'swap', 'capitalize'
    
    Returns:
        str: Converted text
    """
    if mode == 'upper':
        return text.upper()
    elif mode == 'lower':
        return text.lower()
    elif mode == 'title':
        return text.title()
    elif mode == 'swap':
        return text.swapcase()
    elif mode == 'capitalize':
        return text.capitalize()
    else:
        raise ValueError(f"Unknown mode: {mode}")


def main():
    parser = argparse.ArgumentParser(description='English case converter')
    parser.add_argument('text', nargs='?', help='Text to convert (if not provided, reads from stdin)')
    parser.add_argument('-m', '--mode', choices=['upper', 'lower', 'title', 'swap', 'capitalize'], 
                       default='upper', help='Conversion mode (default: upper)')
    parser.add_argument('-f', '--file', help='Input file path')
    parser.add_argument('-o', '--output', help='Output file path')
    
    args = parser.parse_args()
    
    # Get input text
    if args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            print(f"Error: File '{args.file}' not found")
            sys.exit(1)
    elif args.text:
        text = args.text
    else:
        # Read from stdin
        text = sys.stdin.read()
    
    # Convert case
    try:
        result = convert_case(text, args.mode)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    # Output result
    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(result)
            print(f"Result written to '{args.output}'")
        except IOError as e:
            print(f"Error writing to file: {e}")
            sys.exit(1)
    else:
        print(result)


if __name__ == "__main__":
    main()