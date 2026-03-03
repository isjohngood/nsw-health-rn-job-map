import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import re
import time
import json
import os
import random
import sys
from datetime import datetime, date
from urllib.parse import urlparse, parse_qs
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import unicodedata

# Set to True when running --ci (GitHub Actions / non-interactive)
CI_MODE = "--ci" in sys.argv

# Constants
BASE_URL = "https://jobs.health.nsw.gov.au/"
SEARCH_KEYWORD = "Registered Nurse"
OUTPUT_FILE = "rn_jobs_with_incentives.csv"
CONFIG_FILE = "config.json"
PAGE_TIMEOUT = 15
JOB_PAGE_TIMEOUT = 20
RETRIES = 3
TODAY = date.today().isoformat()

# Global variables
jobs = []
global_seen_urls = set()
current_run_urls = set()
new_jobs_count = 0

def load_config():
    """Load configuration from config.json or return defaults."""
    defaults = {
        "FETCH_ALL_DUE_DATES": False,
        "REMOVE_UNLISTED_JOBS": False,
        "ENABLE_ALERT_BEEP": False
    }
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            for key in defaults:
                if key not in config:
                    config[key] = defaults[key]
            print("Loaded configuration from config.json")
            return config
        else:
            print("No config.json found, using defaults")
            return defaults
    except Exception as e:
        print(f"Error loading config: {e}, using defaults")
        return defaults

def save_config(config):
    """Save configuration to config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        print("Saved configuration to config.json")
    except Exception as e:
        print(f"Error saving config: {e}")

def console_ui(config):
    """Display console UI to configure and run the scraper."""
    while True:
        print("\n=== NSW Health Job Scraper ===")
        print(f"Current settings: FETCH_ALL_DUE_DATES={config['FETCH_ALL_DUE_DATES']}, REMOVE_UNLISTED_JOBS={config['REMOVE_UNLISTED_JOBS']}, ENABLE_ALERT_BEEP={config['ENABLE_ALERT_BEEP']}")
        print("New jobs will trigger a console notice.")
        print("Configure options:")
        print("1. Fetch due dates for all jobs? (y/n)")
        print("2. Remove unlisted jobs from CSV? (y/n, warning: may affect trend analysis)")
        print("3. Enable audible beep for available jobs? (y/n)")
        print("4. Start scraping")
        print("5. Exit")
        
        choice = input("Enter choice (1-5): ").strip().lower()
        
        if choice == "1":
            fetch_all = input(f"Fetch due dates for all jobs? (y/n, current {'y' if config['FETCH_ALL_DUE_DATES'] else 'n'}): ").strip().lower()
            if fetch_all in ["y", "n"]:
                config["FETCH_ALL_DUE_DATES"] = fetch_all == "y"
                print(f"Set FETCH_ALL_DUE_DATES to {config['FETCH_ALL_DUE_DATES']}")
            else:
                print("Invalid input, please enter 'y' or 'n'")
        
        elif choice == "2":
            remove_unlisted = input(f"Remove unlisted jobs? (y/n, current {'y' if config['REMOVE_UNLISTED_JOBS'] else 'n'}): ").strip().lower()
            if remove_unlisted in ["y", "n"]:
                config["REMOVE_UNLISTED_JOBS"] = remove_unlisted == "y"
                print(f"Set REMOVE_UNLISTED_JOBS to {config['REMOVE_UNLISTED_JOBS']}")
                if config["REMOVE_UNLISTED_JOBS"]:
                    print("Warning: Enabling this may delete historical data, affecting trend analysis")
            else:
                print("Invalid input, please enter 'y' or 'n'")
        
        elif choice == "3":
            enable_beep = input(f"Enable audible beep for available jobs? (y/n, current {'y' if config['ENABLE_ALERT_BEEP'] else 'n'}): ").strip().lower()
            if enable_beep in ["y", "n"]:
                config["ENABLE_ALERT_BEEP"] = enable_beep == "y"
                print(f"Set ENABLE_ALERT_BEEP to {config['ENABLE_ALERT_BEEP']}")
            else:
                print("Invalid input, please enter 'y' or 'n'")
        
        elif choice == "4":
            print(f"Starting scrape with FETCH_ALL_DUE_DATES={config['FETCH_ALL_DUE_DATES']}, REMOVE_UNLISTED_JOBS={config['REMOVE_UNLISTED_JOBS']}, ENABLE_ALERT_BEEP={config['ENABLE_ALERT_BEEP']}")
            save_config(config)
            return True, config
        
        elif choice == "5":
            print("Exiting...")
            return False, config
        
        else:
            print("Invalid choice, please enter 1, 2, 3, 4, or 5")

def get_session_id(url):
    """Extract session ID from URL."""
    parsed = urlparse(url)
    path = parsed.path.split("/")
    for part in path:
        if part.isdigit():
            return part
    return None

def get_total_pages(soup):
    """Extract total number of pages from pagination container."""
    try:
        num_pages_elem = soup.find("div", id="jPaginateNumPages")
        if num_pages_elem:
            return int(float(num_pages_elem.text.strip()))
        pagination = soup.find("div", class_="pagination")
        if pagination:
            page_links = pagination.find_all("a", attrs={"data-page": re.compile(r"\d+")})
            if page_links:
                return max(int(link.get("data-page")) for link in page_links)
        return 1
    except Exception as e:
        print(f"Error extracting total pages: {e}")
        return 1

def fetch_job_details(driver, url, fetch_incentives=True):
    """Fetch incentives (if requested) and due date from job details page."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log = []  # Initialize debug_log
    raw_text = "No text extracted"
    page_text = "No text extracted"
    description = ""  # Initialize description
    
    try:
        driver.set_page_load_timeout(JOB_PAGE_TIMEOUT)
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(0.5, 1.5))
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        # Extract raw text with preserved formatting
        try:
            raw_text = soup.get_text(separator="", strip=False)
            debug_log.append("Raw text extraction completed")
        except Exception as e:
            debug_log.append(f"Raw text extraction error: {str(e)}")
        
        try:
            page_text = unicodedata.normalize("NFKC", raw_text).lower()
            page_text = re.sub(r"[^\w\s/,-of,'’()]", " ", page_text)  # Preserve 'of', /, -, ,, ', ’
            page_text = re.sub(r"\s+", " ", page_text)  # Normalize whitespace
            debug_log.append(f"Text preprocessing completed. Preserved characters: '’")
        except Exception as e:
            debug_log.append(f"Preprocessing error: {str(e)}")
            page_text = raw_text.lower()
        
        # Set description, prioritizing div.job_description
        try:
            description_elem = soup.select_one("div.job_description")
            description = description_elem.get_text(separator="", strip=False).lower() if description_elem else page_text
            debug_log.append("Description extraction completed")
            # Extract italicized text separately
            italic_texts = [elem.get_text(separator="", strip=False).lower() for elem in description_elem.find_all(['i', 'em'])] if description_elem else []
            italicized_content = " ".join(italic_texts) if italic_texts else ""
            debug_log.append(f"Italicized texts found (length: {len(italicized_content)}): {italicized_content[:200]}...")
        except Exception as e:
            debug_log.append(f"Description extraction error: {str(e)}")
            description = page_text
            italic_texts = []
            italicized_content = ""
        
        due_date = "N/A"
        all_dates = []
        
        # Define date patterns
        date_patterns = [
            (r"\d{2}/\d{2}/\d{4}", 0),
            (r"\d{2}-\d{2}-\d{4}", 0),
            (r"(?:[A-Za-z]+,\s*)?\d{1,2}(?:st|nd|rd|th)?\s*(?:of\s+)?[A-Za-z]+\s+\d{4}", 0),
            (r"\d{1,2}(?:st|nd|rd|th)?\s+of\s+[A-Za-z]+\s+\d{4}", 0),
            (r"(?:[A-Za-z]+)?\s*\d{1,2}\s+[A-Za-z]+\s+(?:at\s+\d{1,2}:\d{2}\s*(?:am|pm)\s*)?\d{4}", 0),
            (r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", 0),
            (r"[A-Za-z]{3}\s+\d{1,2}(?:st|nd|rd|th)?,\s*\d{4}", 0),
            (r"[A-Za-z]+\s+\d{1,2},\s*\d{4}", 0),
            (r"\d{2},\d{2},\d{4}", 0),
            (r"[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th),\s*\d{4}", 0),
            (r"\d{1,2}-[A-Za-z]{3}-\d{4}", 0)
        ]
        
        # Extract all dates
        try:
            for pattern, group in date_patterns:
                for match in re.finditer(pattern, page_text, re.I):
                    date_str = match.group(group)
                    start_pos = max(0, match.start() - 100)
                    end_pos = min(len(page_text), match.end() + 100)
                    context = page_text[start_pos:end_pos]
                    all_dates.append((date_str, context))
                    debug_log.append(f"Found date: {date_str} (Context: {context})")
        except Exception as e:
            debug_log.append(f"Date extraction error: {str(e)}")
        
        # Select the most likely due date
        due_date_keywords = ["closing", "application", "applications", "close", "due", "deadline"]
        selected_due_date = None
        selected_context = None
        min_keyword_distance = float('inf')
        
        req_id_pos = page_text.find("requisition id")
        
        for date_str, context in all_dates:
            date_pos = page_text.find(date_str)
            if req_id_pos != -1 and date_pos < req_id_pos:
                continue
            for keyword in due_date_keywords:
                keyword_pos = context.find(keyword)
                if keyword_pos != -1:
                    context_date_pos = context.find(date_str)
                    distance = abs(keyword_pos - context_date_pos)
                    if distance < min_keyword_distance:
                        min_keyword_distance = distance
                        selected_due_date = date_str
                        selected_context = context
        
        if not selected_due_date and all_dates:
            for date_str, context in all_dates:
                date_pos = page_text.find(date_str)
                if req_id_pos == -1 or date_pos >= req_id_pos:
                    selected_due_date = date_str
                    selected_context = context
                    debug_log.append(f"No due date keywords found, selecting first date after requisition ID: {selected_due_date} (Context: {selected_context})")
                    break
        
        if selected_due_date:
            due_date = selected_due_date
            debug_log.append(f"Selected due date: {due_date} (Context: {selected_context})")
        
        incentives = "N/A"
        unmatched_incentives = []
        scheme_matches = []
        scheme_incentives = []
        general_incentives = []
        if fetch_incentives:
            debug_log.append(f"Incentive fetching enabled for {url}")
            # Check for "incentive" in description or italicized content
            has_incentive = "incentive" in description or "incentive" in italicized_content
            debug_log.append(f"Incentive keyword found: {has_incentive}")
            
            if has_incentive:
                # Scheme-specific pattern
                try:
                    scheme_incentives = re.findall(
                        r".*?up to\s*(?:the value of\s*)?\$[\d,]+.*?nsw\s*rural\s*health\s*workforce\s*incentive\s*scheme",
                        description,
                        re.I | re.DOTALL
                    )
                    if scheme_incentives:
                        scheme_matches = [re.sub(r"\s+", " ", m.strip()) for m in scheme_incentives]
                        debug_log.append(f"Scheme incentives matched: {scheme_matches}")
                except Exception as e:
                    debug_log.append(f"Scheme incentives regex error: {str(e)}")
                
                # Try italicized content if no matches
                if not scheme_incentives and italicized_content:
                    try:
                        italic_matches = re.findall(
                            r".*?up to\s*(?:the value of\s*)?\$[\d,]+.*?nsw\s*rural\s*health\s*workforce\s*incentive\s*scheme",
                            italicized_content,
                            re.I | re.DOTALL
                        )
                        if italic_matches:
                            scheme_matches.extend([re.sub(r"\s+", " ", m.strip()) for m in italic_matches])
                            debug_log.append(f"Italicized scheme incentives matched: {italic_matches}")
                    except Exception as e:
                        debug_log.append(f"Italicized scheme regex error: {str(e)}")
                
                # General incentives pattern
                try:
                    general_incentives = re.findall(
                        r"(?:incentive|bonus|relocation|salary packaging|allowance|retention|recruitment|annual|payment|benefit)\s*.*?(?:\$[\d,]+(?:\.\d+)?|\d+%|up to\s*(?:the value of\s*)?[^.]*?)",
                        description,
                        re.I | re.DOTALL
                    )
                except Exception as e:
                    debug_log.append(f"General incentives regex error: {str(e)}")
                
                # Combine and deduplicate matches
                incentives_list = list(set(
                    [re.sub(r"\s+", " ", m.strip()) for m in scheme_incentives + general_incentives
                     if not re.search(r"@health|about us|requisition id|requirements:|current authority|collaborate with|take-home pay|salary package a range|tingha mps|health service|work alongside", m, re.I)]
                ))
                
                if incentives_list:
                    incentives = "; ".join(incentives_list) if incentives_list else "Incentives Offered"
                else:
                    # Log potential incentives for debugging
                    try:
                        potential_incentives = re.findall(
                            r"(?:incentive|bonus|relocation|salary packaging|allowance|retention|recruitment|annual|payment|benefit|nsw\s*rural\s*health\s*workforce\s*incentive\s*scheme)\s*.*?(?=\.\s|$)",
                            description,
                            re.I | re.DOTALL
                        )
                        unmatched_incentives = [re.sub(r"\s+", " ", m.strip()) for m in potential_incentives]
                        debug_log.append(f"No incentives matched. Potential incentives: {unmatched_incentives}")
                        if not (scheme_incentives or general_incentives) and unmatched_incentives:
                            debug_log.append(f"Full description for debugging: {description[:500]}...")
                            if italicized_content:
                                debug_log.append(f"Italicized content for debugging: {italicized_content[:500]}...")
                    except Exception as e:
                        debug_log.append(f"Unmatched incentives regex error: {str(e)}")
            else:
                debug_log.append("No incentive keyword found, skipping incentive extraction")
        
        # Log all extracted dates and incentives to a separate file
        with open(f"extracted_dates_{timestamp}.txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {url}\n")
            f.write("Extracted Dates:\n")
            f.write("================\n")
            if all_dates:
                for i, (date_str, context) in enumerate(all_dates, 1):
                    f.write(f"{i}. Date: {date_str}\n   Context: {context}\n")
            else:
                f.write("No dates found.\n")
            f.write("\nSelected Due Date:\n")
            f.write(f"{due_date} (Context: {selected_context if selected_context else 'None'})\n")
            f.write("\nExtracted Incentives:\n")
            f.write(f"{incentives}\n")
            f.write("\nScheme-Specific Incentives:\n")
            f.write("\n".join(scheme_matches) if scheme_matches else "None\n")
            f.write("\nUnmatched Incentives:\n")
            f.write("\n".join(unmatched_incentives) if unmatched_incentives else "None\n")
            f.write("\nPreprocessing Log:\n")
            f.write(f"Raw text length: {len(raw_text)}\n")
            f.write(f"Normalized text length: {len(page_text)}\n")
            f.write(f"Sample normalized text: {page_text[:200]}...\n")
            f.write(f"\nRaw description (first 500 chars):\n{description[:500]}...\n")
            try:
                if description_elem:
                    f.write(f"\nHTML snippet of div.job_description:\n{str(description_elem)[:500]}...\n")
            except NameError:
                f.write("\nHTML snippet of div.job_description: Not available\n")
            scheme_pos = description.find("nsw rural health workforce incentive scheme")
            if scheme_pos != -1:
                f.write(f"\nText around scheme (200 chars before/after):\n{description[max(0, scheme_pos-200):scheme_pos+200]}...\n")
            if not (scheme_incentives or general_incentives):
                f.write(f"\nFull description (first 500 chars):\n{description[:500]}...\n")
        
        print(f"Extracted for {url}: Due Date={due_date}, Incentives={incentives}")
        return due_date, incentives
    except (TimeoutException, WebDriverException) as e:
        debug_log.append(f"Exception occurred: {str(e)}")
        print(f"Retry {attempt+1}/{RETRIES} for {url}: {e}")
        with open(f"error_job_page_{timestamp}_attempt_{attempt+1}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        time.sleep(random.uniform(1, 2))
    except Exception as e:
        debug_log.append(f"Unexpected exception: {str(e)}")
        print(f"Unexpected error for {url}: {e}")
    
    # Log page text and debug info if due date extraction fails
    with open("failed_urls.txt", "a", encoding="utf-8") as f:
        f.write(f"{url}\n")
    with open(f"failed_text_{timestamp}.txt", "w", encoding="utf-8") as f:
        f.write(f"Raw page text:\n{raw_text}\n\nNormalized page text:\n{page_text}\n\nDebug log:\n")
        f.write("\n".join(debug_log) if debug_log else "No patterns matched")
    print(f"Failed to extract due date for {url}. Page text and debug log saved to failed_text_{timestamp}.txt")
    return "N/A", "N/A"

def update_missing_due_dates(driver, config):
    """Check CSV for jobs with missing due dates and fetch them."""
    if not os.path.exists(OUTPUT_FILE):
        print(f"{OUTPUT_FILE} does not exist. No due dates to update.")
        return
    
    try:
        df = pd.read_csv(OUTPUT_FILE, encoding="utf-8")
        if 'Due Date' not in df.columns or 'URL' not in df.columns:
            print("CSV missing required columns.")
            return
        
        missing_due_dates = df[df['Due Date'].isna() | (df['Due Date'] == "N/A")]
        if missing_due_dates.empty:
            print("No jobs with missing due dates found.")
            return
        
        print(f"Found {len(missing_due_dates)} jobs with missing due dates. Updating...")
        
        for index, row in missing_due_dates.iterrows():
            job_url = row['URL']
            job_has_incentives = "Incentives Offered" in str(row.get('Job Title', '')) or \
                                "Incentives Offered" in str(row.get('Incentives', ''))
            
            try:
                due_date, incentives = fetch_job_details(
                    driver, job_url, fetch_incentives=True
                )
                
                if due_date != "N/A":
                    df.at[index, 'Due Date'] = due_date
                    if incentives != "N/A" and row.get('Incentives', "N/A") == "N/A":
                        df.at[index, 'Incentives'] = incentives
                    print(f"Updated due date for {row['Job Title']}: {due_date}")
                    # Save CSV after each successful update
                    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
                    print(f"Saved updated CSV after processing {job_url}")
            
            except Exception as e:
                print(f"Error processing {job_url}: {e}")
                continue  # Continue with next job
            
            time.sleep(random.uniform(1, 2))
        
        print(f"Completed updating due dates. Final CSV saved.")
    
    except Exception as e:
        print(f"Error reading or processing CSV: {e}")

def process_page(driver, page_num, current_url, config):
    """Process a single page of job listings."""
    global new_jobs_count
    print(f"Processing page {page_num} (URL: {current_url})")
    
    try:
        driver.get(current_url)
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.job_list_row"))
        )
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(0.5, 1.5))
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        job_containers = soup.select("div.job_list_row")
        
        if not job_containers:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            error_file = f"no_jobs_page_{page_num}_{timestamp}.html"
            with open(error_file, "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print(f"No job containers found on page {page_num}. Saved to {error_file}")
            return False, None
        
        print(f"Found {len(job_containers)} job containers")
        
        page_urls = []
        for job in job_containers:
            try:
                job_id = job.get("id")
                title_elem = job.select_one("div.jlr_title p a.job_link")
                title = title_elem.text.strip() if title_elem else None
                url = title_elem.get("href") if title_elem else ""
                
                if not title or title.lower() in ["n/a", "filter results"]:
                    print(f"Skipped job due to title: {title}")
                    continue
                
                if url and not url.startswith("http"):
                    url = "https://jobs.health.nsw.gov.au" + url
                
                page_urls.append((job_id, title, url))
            except Exception as e:
                print(f"Error extracting job container: {e}")
                continue
        
        current_run_urls.update(url for _, _, url in page_urls)
        
        new_urls = [url for _, _, url in page_urls if url not in global_seen_urls]
        if not new_urls:
            print(f"Page {page_num} contains only seen jobs, continuing to next page")
        
        for job_id, title, url in page_urls:
            if url in global_seen_urls:
                print(f"Skipped duplicate job: {title} (ID: {job_id})")
                continue
            
            try:
                job_elem = soup.find("div", id=job_id)
                location_elem = job_elem.select_one("p.jlr_cat_loc span.location") if job_elem else None
                location = location_elem.text.strip() if location_elem else "N/A"
                
                incentives = "N/A"
                due_date = "N/A"
                job_has_incentives = re.search(r"incentives\s*offered", title, re.I) is not None
                print(f"Job title: {title}, Incentives Offered detected: {job_has_incentives}")
                
                due_date, job_incentives = fetch_job_details(driver, url, fetch_incentives=True)
                incentives = job_incentives if job_incentives != "N/A" else "N/A"
                
                jobs.append({
                    "Job Title": title,
                    "Location": location,
                    "Incentives": incentives,
                    "Due Date": due_date,
                    "URL": url,
                    "Scraped Date": TODAY,
                    "Last Seen": TODAY
                })
                global_seen_urls.add(url)
                new_jobs_count += 1
                print(f"{' ' if not config['ENABLE_ALERT_BEEP'] else '\a'}", end="")
                print(f"*** NEW JOB DETECTED *** Title: {title}, ID: {job_id}, URL: {url}")
                print(f"Added job: {title} (ID: {job_id})")
            except Exception as e:
                print(f"Error processing job {title} (ID: {job_id}): {e}")
        
        save_jobs()
        total_pages = get_total_pages(soup)
        return True, total_pages
    except Exception as e:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        error_file = f"error_page_{page_num}_{timestamp}.html"
        with open(error_file, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"Error processing page {page_num}: {e}. Saved to {error_file}")
        return False, None

def save_jobs():
    """Save jobs to CSV, deduplicate, and handle unlisted jobs."""
    global new_jobs_count
    if not jobs:
        print("No jobs to save.")
        return
    
    # Define expected columns
    columns = ["Job Title", "Location", "Incentives", "Due Date", "URL", "Scraped Date", "Last Seen"]
    
    new_df = pd.DataFrame(jobs)
    
    if os.path.exists(OUTPUT_FILE):
        try:
            existing_df = pd.read_csv(OUTPUT_FILE, encoding="utf-8")
            
            # Ensure all expected columns exist in existing_df
            for col in columns:
                if col not in existing_df.columns:
                    existing_df[col] = pd.NA
                    print(f"Added missing column {col} to existing CSV")
            
            if config["REMOVE_UNLISTED_JOBS"]:
                initial_count = len(existing_df)
                existing_df = existing_df[existing_df["URL"].isin(current_run_urls)]
                removed_count = initial_count - len(existing_df)
                print(f"Removed {removed_count} unlisted jobs")
            
            # Update Last Seen for existing jobs
            existing_df["Last Seen"] = existing_df["Last Seen"].fillna(TODAY)
            for url in existing_df["URL"]:
                if url in current_run_urls:
                    existing_df.loc[existing_df["URL"] == url, "Last Seen"] = TODAY
            
            # Ensure new_df has all columns
            for col in columns:
                if col not in new_df.columns:
                    new_df[col] = pd.NA
                    print(f"Added missing column {col} to new jobs DataFrame")
            
            # Concatenate and deduplicate
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            combined_df = combined_df.drop_duplicates(subset=["URL"], keep="last")
            
            # Reorder columns
            combined_df = combined_df[columns]
            
            print(f"Saving CSV with columns: {combined_df.columns.tolist()}")
            combined_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
            print(f"Appended {len(jobs)} new jobs to {OUTPUT_FILE}, total {len(combined_df)} jobs")
            print(f"Total new jobs detected this run: {new_jobs_count}")
        except Exception as e:
            print(f"Error appending to CSV: {e}")
            # Fallback: save new jobs only with all columns
            new_df = new_df[columns]
            new_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
            print(f"Overwrote {OUTPUT_FILE} with {len(jobs)} jobs due to error")
            print(f"Total new jobs detected this run: {new_jobs_count}")
    else:
        # Save new CSV with all columns
        new_df = new_df[columns]
        new_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
        print(f"Saved {len(jobs)} jobs to new {OUTPUT_FILE}")
        print(f"Total new jobs detected this run: {new_jobs_count}")

def main():
    """Main function to orchestrate scraping."""
    global config
    config = load_config()

    if CI_MODE:
        print("Running in CI mode — skipping interactive menu, using config.json settings.")
        should_run = True
    else:
        should_run, config = console_ui(config)

    if not should_run:
        return
    
    options = webdriver.ChromeOptions()
    # NOTE: Headless mode is blocked by the NSW Health site's bot detection.
    # Run without --headless for reliable job loading.
    # options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    # Load existing CSV
    if os.path.exists(OUTPUT_FILE):
        try:
            existing_df = pd.read_csv(OUTPUT_FILE, encoding="utf-8")
            if "URL" in existing_df.columns:
                global_seen_urls.update(existing_df["URL"].dropna().astype(str))
                print(f"Loaded {len(global_seen_urls)} existing URLs from {OUTPUT_FILE}")
        except Exception as e:
            print(f"Error reading existing CSV: {e}")
    
    try:
        # Update missing due dates
        print("Checking for jobs with missing due dates...")
        update_missing_due_dates(driver, config)
        
        # Scrape new jobs — search via homepage form to create a fresh session
        print(f"Navigating to {BASE_URL} and searching for '{SEARCH_KEYWORD}'...")
        driver.get(BASE_URL)
        time.sleep(3)
        try:
            search_input = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "keyword"))
            )
            search_input.clear()
            search_input.send_keys(SEARCH_KEYWORD)
            driver.find_element(By.CSS_SELECTOR, ".search_btn").click()
        except Exception as e:
            print(f"Search form error: {e}. Falling back to direct search URL.")
            driver.get(f"https://jobs.health.nsw.gov.au/jobs/search?q={SEARCH_KEYWORD.replace(' ', '+')}")

        time.sleep(8)
        current_url = driver.current_url
        print(f"Search session URL: {current_url}")

        try:
            alert = driver.switch_to.alert
            print(f"Alert found: {alert.text}")
            alert.accept()
        except:
            print("No alert found")

        page = 1
        total_pages = None
        while True:
            if page == 1:
                page_url = current_url  # Use the session URL from the search
            else:
                session_id = get_session_id(current_url)
                page_url = f"https://jobs.health.nsw.gov.au/jobs/search/{session_id}/page{page}"
            
            continue_processing, detected_total_pages = process_page(driver, page, page_url, config)
            if not continue_processing:
                break
            
            if detected_total_pages is not None:
                total_pages = detected_total_pages
            
            if total_pages is not None and page >= total_pages:
                print(f"Reached detected total pages ({total_pages})")
                break
            
            current_url = page_url
            page += 1
            time.sleep(random.uniform(1, 2))
    
    finally:
        save_jobs()
        driver.quit()

if __name__ == "__main__":
    main()
