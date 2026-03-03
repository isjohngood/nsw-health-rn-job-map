import pandas as pd
import folium
from folium.plugins import MarkerCluster
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from dateutil import parser
import re
from datetime import datetime, date
import pickle
import os
import logging
from tqdm import tqdm
import hashlib
import numpy as np
from html import escape

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('job_map_plotter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize geocoder
geolocator = Nominatim(user_agent="job_map_plotter")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

# Cache and state files
GEO_CACHE = "geocode_cache.pkl"
CSV_HASH = "csv_hash.pkl"
PREVIOUS_URLS = "previous_urls.pkl"

# Output directory
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_cache(file):
    """Load cache from file."""
    try:
        if os.path.exists(file):
            logger.debug(f"Loading cache from {file}")
            with open(file, "rb") as f:
                return pickle.load(f)
    except Exception as e:
        logger.error(f"Failed to load cache {file}: {e}")
    return {} if "geocode" in file else {"hash": ""} if "csv_hash" in file else {"urls": set()}

def save_cache(cache, file):
    """Save cache to file."""
    logger.debug(f"Saving cache to {file}")
    try:
        with open(file, "wb") as f:
            pickle.dump(cache, f)
    except Exception as e:
        logger.error(f"Failed to save cache {file}: {e}")

def get_file_hash(file_path):
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def clean_location(location, idx, row):
    """Extract primary city/town from location string, logging invalid values."""
    if pd.isna(location) or location == "N/A" or location == "" or location is None:
        logger.warning(f"Row {idx}: Invalid location: {location}, Row data: {row.to_dict()}")
        return None
    try:
        location = str(location).strip()
        if not location:
            logger.warning(f"Row {idx}: Empty location, Row data: {row.to_dict()}")
            return None
        # Handle specific cases
        if "negotiable" in location.lower():
            logger.warning(f"Row {idx}: Non-geographic location: {location}, Row data: {row.to_dict()}")
            return None
        # Split by '|' or ',' and take first part
        primary = location.split("|")[0].split(",")[0].strip()
        # Handle phrases like "and X additional locations"
        primary = re.split(r"\s*and\s+|\s*[+]\s*", primary)[0].strip()
        # Remove facility prefixes/suffixes
        match = re.search(r"^(?:[A-Z]{1,5}\s+)?(.+?)(?:\s*(?:Hospital|Community|Health|Centre|Clinic|Justice).*|$)", primary, re.IGNORECASE)
        cleaned = match.group(1).strip() if match.group(1).strip() else None
        # Check for non-geographic terms
        invalid_patterns = r"^(multiple|various|additional|locations|unknown|other|negotiable)$"
        if cleaned and re.match(invalid_patterns, cleaned.lower()):
            logger.warning(f"Row {idx}: Non-geographic location: {cleaned}, Original: {location}, Row data: {row.to_dict()}")
            return None
        if not cleaned:
            logger.warning(f"Row {idx}: Cleaned location is empty: {location}, Row data: {row.to_dict()}")
            return None
        return cleaned
    except Exception as e:
        logger.error(f"Row {idx}: Error cleaning location '{location}': {e}, Row data: {row.to_dict()}")
        return None

def get_coordinates(location, cache):
    """Geocode location to (latitude, longitude)."""
    if not location:
        logger.warning("No location provided for geocoding")
        return None
    if location in cache:
        logger.debug(f"Using cached coordinates for {location}")
        return cache[location]
    logger.debug(f"Geocoding location: {location}")
    try:
        result = geocode(f"{location}, NSW, Australia")
        if result:
            coords = (result.latitude, result.longitude)
            cache[location] = coords
            save_cache(cache, GEO_CACHE)
            logger.debug(f"Geocoded {location} to {coords}")
            return coords
        logger.debug(f"Full query failed, trying location alone: {location}")
        result = geocode(location)
        if result:
            coords = (result.latitude, result.longitude)
            cache[location] = coords
            save_cache(cache, GEO_CACHE)
            logger.debug(f"Geocoded {location} (fallback) to {coords}")
            return coords
        logger.warning(f"Geocoding failed for {location}: No coordinates found")
        with open(os.path.join(OUTPUT_DIR, "failed_geocodes.txt"), "a") as f:
            f.write(f"{location}\n")
        return None
    except Exception as e:
        logger.error(f"Geocoding error for {location}: {e}")
        return None

def is_alert_job(row, previous_urls):
    """Check if job needs an alert (new or incentives in Job Title or Incentives)."""
    is_new = row['URL'] not in previous_urls
    title = row['Job Title']
    incentives = row['Incentives']
    has_incentives = (isinstance(title, str) and "incentive" in title.lower()) or \
                     (isinstance(incentives, str) and "incentive" in incentives.lower())
    logger.debug(f"Job {row['Job Title']}: is_new={is_new}, has_incentives={has_incentives}")
    return is_new or has_incentives, is_new, has_incentives

def parse_due_date(date_str):
    """Parse various due date formats."""
    if date_str == "N/A" or pd.isna(date_str) or date_str == "":
        return None
    try:
        date_str = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", date_str, flags=re.IGNORECASE)
        date_str = re.sub(r"^[a-zA-Z]+,\s*", "", date_str)  # strip "Thursday, " prefix
        return parser.parse(date_str, dayfirst=True)
    except Exception as e:
        logger.warning(f"Failed to parse due date '{date_str}': {e}")
        return None

def is_expired(due_date):
    """Check if job is expired based on due date."""
    if due_date is None:
        return False
    today = date.today()
    return due_date.date() < today

def create_job_map(df, geo_cache, previous_urls):
    """Create an interactive map with clustered job locations, an Incentives filter, and a table for jobs with no location."""
    logger.debug("Creating Folium map")
    m = folium.Map(location=[-33.8688, 151.2093], zoom_start=7,
                   tiles="OpenStreetMap")

    # Initialize marker cluster (control=False hides it from the legend)
    logger.debug("Adding marker cluster")
    marker_cluster = MarkerCluster(name="All Jobs", control=False).add_to(m)
    
    total_rows = len(df)
    location_jobs = {}
    alert_count = 0
    new_count = 0
    incentives_count = 0
    expired_count = 0
    missing_location_rows = []
    total_markers = 0
    incentives_markers = 0
    
    # Group jobs by coordinates
    for idx, row in tqdm(df.iterrows(), total=total_rows, desc="Processing jobs"):
        logger.info(f"Processing job {idx + 1}/{total_rows}: {row['Job Title']}")
        location = clean_location(row['Location'], idx, row)
        if not location:
            missing_location_rows.append(row)
            logger.warning(f"Skipping job {idx + 1}: Invalid location")
            continue
        
        coords = get_coordinates(location, geo_cache)
        if not coords:
            missing_location_rows.append(row)
            logger.warning(f"Skipping job {idx + 1}: No coordinates for {location}")
            continue
        
        is_alert, is_new, has_incentives = is_alert_job(row, previous_urls)
        due_date = parse_due_date(row['Due Date'])
        is_expired_job = is_expired(due_date)
        
        if is_alert:
            alert_count += 1
            if is_new:
                new_count += 1
            if has_incentives:
                incentives_count += 1
        if is_expired_job:
            expired_count += 1
            continue  # skip expired jobs from the map

        coords_key = (coords[0], coords[1])
        if coords_key not in location_jobs:
            location_jobs[coords_key] = []
        location_jobs[coords_key].append((row, is_alert, is_new, has_incentives, is_expired_job, location))
    
    # Save missing location rows
    if missing_location_rows:
        missing_df = pd.DataFrame(missing_location_rows)
        missing_df.to_csv(os.path.join(OUTPUT_DIR, "debug_missing_locations.csv"), index=False, encoding="utf-8")
        logger.info(f"Saved {len(missing_location_rows)} rows with missing/invalid locations to {os.path.join(OUTPUT_DIR, 'debug_missing_locations.csv')}")
    
    # Create markers and assign to layers
    for coords, jobs in location_jobs.items():
        popup_content = ""
        any_alert = any(job[1] for job in jobs)
        for idx, (row, is_alert, is_new, has_incentives, is_expired_job, location) in enumerate(jobs, 1):
            alert_text = "⚠️ ALERT: " if is_alert else ""
            if is_alert:
                reasons = []
                if is_new:
                    reasons.append("New Job")
                if has_incentives:
                    reasons.append("Incentives Offered")
                alert_text += ", ".join(reasons)
            
            def safe_str(value):
                return escape(str(value)) if pd.notna(value) and value is not None else "Unknown"
            
            popup_content += f"""
            <b>Job {idx}:</b><br>
            {alert_text}<br>
            <b>Job Title:</b> {safe_str(row['Job Title'])}<br>
            <b>Location:</b> {safe_str(row['Location'])}<br>
            <b>Incentives:</b> {"Yes" if has_incentives else "No"}<br>
            <b>Due Date:</b> {safe_str(row['Due Date'])}<br>
            <b>Scraped Date:</b> {safe_str(row['Scraped Date'])}<br>
            <b>Last Seen:</b> {safe_str(row['Last Seen'])}<br>
            <a href="{safe_str(row['URL'])}" target="_blank">View Job</a><br><br>
            """
        
        color = "red" if any_alert else "blue" if any(job[3] for job in jobs) else "green"
        
        for job in jobs:
            row, is_alert, is_new, has_incentives, is_expired_job, location = job
            marker = folium.Marker(
                location=coords,
                popup=folium.Popup(popup_content, max_width=400),
                tooltip=f"{clean_location(row['Location'], 0, row)} ({len(jobs)} jobs)",
                icon=folium.Icon(color=color)
            )
            marker_cluster.add_child(marker)
            total_markers += 1
            if has_incentives:
                incentives_markers += 1
    
    # Log marker counts
    logger.info(f"Processed {len(df)} jobs, {len(location_jobs)} unique coordinate sets, {len(missing_location_rows)} missing/invalid locations")
    logger.info(f"Alerts: {alert_count} (New: {new_count}, Incentives: {incentives_count}), Expired: {expired_count}")
    logger.info(f"Total markers: {total_markers}, Incentives markers: {incentives_markers}")
    
    return m

def main():
    logger.info(f"Starting job_map_plotter at {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    geo_cache = load_cache(GEO_CACHE)
    previous_hash = load_cache(CSV_HASH).get("hash", "")
    previous_urls = load_cache(PREVIOUS_URLS).get("urls", set())
    
    if not os.path.exists("rn_jobs_with_incentives.csv"):
        logger.error("CSV file not found: rn_jobs_with_incentives.csv")
        return
    
    current_hash = get_file_hash("rn_jobs_with_incentives.csv")
    logger.info(f"CSV hash: {current_hash}, Previous hash: {previous_hash}")
    
    try:
        logger.debug("Reading rn_jobs_with_incentives.csv")
        df = pd.read_csv("rn_jobs_with_incentives.csv", encoding="utf-8")
        logger.info(f"Loaded CSV with {len(df)} rows")
        
        # Pre-validate locations
        unique_locations = df["Location"].apply(clean_location, args=(0, df.iloc[0])).dropna().unique()
        logger.info(f"Found {len(unique_locations)} unique locations")
        for loc in unique_locations:
            if not loc or re.match(r"^(multiple|various|additional|locations|unknown|other|negotiable)$", loc.lower(), re.IGNORECASE):
                logger.warning(f"Potentially invalid location found: {loc}")
        
        # Clean data
        df["Location"] = df["Location"].replace(["N/A", ""], pd.NA)
        df = df.dropna(subset=["Location"])
        logger.info(f"After cleaning locations, {len(df)} rows remain")
        
        # Check for duplicates
        duplicates = df[df["URL"].duplicated(keep=False)]
        if not duplicates.empty:
            logger.warning(f"Found {len(duplicates)} duplicate URLs, keeping first occurrence")
            duplicates.to_csv(os.path.join(OUTPUT_DIR, "duplicate_urls.csv"), index=False)
            for url in duplicates["URL"].unique():
                logger.debug(f"Duplicate URL: {url}")
        df = df.drop_duplicates(subset="URL", keep="first")
        logger.info(f"After deduplication, {len(df)} unique jobs remain")
        
        expected_columns = ["Job Title", "Location", "Incentives", "Due Date", "URL", "Scraped Date", "Last Seen"]
        if not all(col in df.columns for col in expected_columns):
            logger.error(f"Missing columns. Found: {list(df.columns)}, Expected: {expected_columns}")
            return
        
        logger.debug("Generating map")
        job_map = create_job_map(df, geo_cache, previous_urls)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        output_file = os.path.join(OUTPUT_DIR, f"job_map_{timestamp}.html")
        job_map.save(output_file)
        logger.info(f"Map saved as {output_file}")
        
        # Update state
        save_cache({"hash": current_hash}, CSV_HASH)
        save_cache({"urls": set(df['URL'])}, PREVIOUS_URLS)
        logger.debug("Updated CSV hash and previous URLs")
    
    except Exception as e:
        logger.error(f"Error processing CSV: {e}", exc_info=True)
        raise
    finally:
        logger.info("Program completed")

if __name__ == "__main__":
    main()
