# English Case Converter

A simple Python script to convert text case in various ways.

## Features

- Convert to uppercase
- Convert to lowercase
- Convert to title case
- Swap case (uppercase ↔ lowercase)
- Capitalize first letter of each word

## Usage

### Basic usage
```bash
python case_converter.py "Hello World"
```

### Specify conversion mode
```bash
# Convert to lowercase
python case_converter.py -m lower "HELLO WORLD"

# Convert to title case
python case_converter.py -m title "hello world"

# Swap case
python case_converter.py -m swap "Hello World"
```

### Read from file and write to file
```bash
# Read from input.txt, convert to uppercase, write to output.txt
python case_converter.py -f input.txt -m upper -o output.txt
```

### Pipe input
```bash
echo "Hello World" | python case_converter.py -m lower
```

## Modes

- `upper`: Convert all characters to uppercase
- `lower`: Convert all characters to lowercase
- `title`: Convert first character of each word to uppercase
- `swap`: Swap uppercase and lowercase characters
- `capitalize`: Convert first character to uppercase, rest to lowercase

## Examples

```bash
$ python case_converter.py "Hello World"
HELLO WORLD

$ python case_converter.py -m lower "HELLO WORLD"
hello world

$ python case_converter.py -m title "hello world"
Hello World

$ python case_converter.py -m swap "Hello World"
hELLO wORLD
```

## License

MIT License