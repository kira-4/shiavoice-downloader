import re
import os
import hashlib
import logging

logger = logging.getLogger("shiavoice.utils")

def setup_logging(verbose: bool = False, log_file: str = None):
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True
    )

def sanitize_filename(name: str, strict: bool = False) -> str:
    """Sanitize string for filesystem usage."""
    if not name:
        return "unknown"
    # Basic sanitize
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.strip()
    if strict:
        name = re.sub(r'[^\w\s\.-]', '', name)
        name = name.strip()
    if not name:
        return "unknown"
    return name

def parse_hijri_year(text: str) -> str:
    """
    Parse Hijri year from text like '٣/ربيع الثاني/١٤٢٦ هـ' and convert to Gregorian.
    Returns the year as a string or None.
    """
    if not text:
        return None
        
    arabic_digits_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    try:
        text = text.translate(arabic_digits_map)
        match = re.search(r'(\d{4})', text)
        if match:
            hijri = int(match.group(1))
            # Approx conversion: G = (H * 0.970224) + 621.5774
            gregorian = round((hijri * 0.970224) + 621.5774)
            return str(gregorian)
    except Exception:
        pass
    return None

def get_covers_cache_path(url: str, cache_dir: str) -> str:
    """Get the local cache path for a cover URL."""
    if not url or not cache_dir:
        return None
    hashed = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(cache_dir, hashed + ".jpg")
