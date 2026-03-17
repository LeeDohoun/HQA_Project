# Toss Invest Community Crawler (토스증권 종토방 크롤러)

KOSPI 상위 500개 종목의 토스증권 커뮤니티(종토방) 게시글을 자동으로 수집하는 크롤러입니다.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browsers
playwright install chromium
```

## Usage

```bash
# STEP 1 (Recommended): Run API discovery on Samsung first
python main.py --discover A005930

# STEP 2: Test with a single stock
python main.py --stock A005930

# STEP 3: Crawl top 50 stocks
python main.py --top 50

# STEP 4: Full crawl (top 500)
python main.py

# Resume after interruption (automatic)
python main.py

# Fresh start (ignore checkpoint)
python main.py --fresh

# Export existing data to Excel
python main.py --export-only
```

## Anti-Blocking Features

- Playwright stealth mode (bypasses bot detection)
- Random human-like delays (2-5s between actions, 5-15s between stocks)
- User-Agent rotation per browser session
- Viewport randomization
- Human-like scrolling with variable speed
- Exponential backoff on errors
- Browser context rotation every 50 stocks
- Error cooldown (2-min pause after 5 consecutive errors)
- Checkpoint/resume support
- Seoul timezone and Korean locale

## Output

All output goes to `./output/`:

| File | Description |
|------|-------------|
| `tossinvest_community_data.csv` | Main data file (streaming write) |
| `tossinvest_community_data.xlsx` | Excel with summary sheet |
| `tossinvest_community_data.json` | JSON format |
| `kospi_top500_stocks.csv` | Cached stock list |
| `checkpoint.json` | Resume checkpoint |
| `crawler.log` | Detailed log |
| `api_discovery_*.json` | API endpoint discovery results |

## Configuration

Edit `config.py` to adjust:
- `TOP_N_STOCKS`: Number of stocks (default: 500)
- `MIN_DELAY` / `MAX_DELAY`: Request delays
- `HEADLESS`: Set `False` to see the browser
- `MAX_SCROLL_ATTEMPTS`: How far to scroll per stock
- `MAX_CONSECUTIVE_ERRORS`: Error threshold before cooldown

## Important Notes

1. **Run discovery first** (`--discover`) to see actual API endpoints
2. The crawler captures both intercepted API responses AND DOM-scraped content
3. After discovery, you can tune the `ApiInterceptor` keywords in `crawler.py`
4. Respect the site's terms of service
5. Total crawl time for 500 stocks: ~4-8 hours depending on settings
