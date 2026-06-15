# Email Validator Script

A Python script to validate email addresses by checking syntax (using RFC 5322 simplified regex) and optionally verifying domain existence via DNS lookup.

## Features

- ✅ **Syntax Validation**: Checks email format against RFC 5322 rules
- 🌐 **DNS Verification**: Optional check if domain has A/AAAA/MX records
- 📁 **Batch Processing**: Validate multiple emails from a file or stdin
- 🔧 **Flexible CLI**: Skip DNS check with `--no-dns` flag
- 📝 **Detailed Output**: Verbose mode shows full validation details
- 🧪 **Comprehensive Tests**: Unit tests with mocked DNS calls

## Installation

```bash
# No external dependencies required for basic usage
git clone <repo-url>
cd email-validator
```

## Usage

### Basic Usage

```bash
# Validate a single email address
python validate_email.py user@example.com

# Skip DNS check (faster, syntax-only)
python validate_email.py user@example.com --no-dns

# Verbose output
python validate_email.py user@example.com --verbose
```

### Batch Processing

```bash
# From a file (one email per line)
python validate_email.py --file emails.txt

# From stdin
echo "user@example.com" | python validate_email.py -

# With quiet mode (for scripting)
python validate_email.py user@example.com --quiet
# Output: valid or invalid
```

## Output Format

- ✅ `user@example.com is VALID` — Syntax and domain are valid
- ❌ `invalid@bad.com is INVALID` — Email failed validation
  - `Syntax: FAIL - Missing '@' symbol`
  - `Domain: FAIL - Domain not found`

## Validation Rules

### Syntax Rules
- Local part: letters, digits, and characters `.!#$%&'*+/=?^_`{|}~-`
- Domain: letters, digits, hyphens, dots; labels can't start/end with hyphen
- TLD: at least 2 alphabetic characters
- Total length: ≤ 254 characters
- Local part length: ≤ 64 characters

### Domain Check
- Resolves A (IPv4) and AAAA (IPv6) records
- 5-second timeout to prevent hanging
- Configurable via `--no-dns` flag

## Testing

```bash
# Run all tests
python -m pytest test_validate_email.py -v

# Run with coverage (if pytest-cov is installed)
python -m pytest test_validate_email.py --cov=validate_email -v
```

## Dependencies

- Python 3.7+
- No external packages required (uses standard library only)
- Optional: `pytest` for running tests

## Project Structure

```
email-validator/
├── validate_email.py      # Main validation script
├── test_validate_email.py # Unit tests
├── README.md              # This file
└── requirements.txt       # Optional dependencies
```

## License

MIT
