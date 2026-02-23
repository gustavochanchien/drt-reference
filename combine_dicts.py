import csv
import json
import re
import os

CEDICT_FILE = 'cedict_ts.u8'
ECDICT_FILE = 'ecdict.csv'
OUTPUT_FILE = 'dictionary.jsonl'

def build_unified_dictionary():
    unified_id = 1
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as out_f:
        
        # ==========================================
        # 1. PARSE CC-CEDICT (Chinese -> English)
        # ==========================================
        print(f"Parsing {CEDICT_FILE}...")
        # Regex handles optional trailing spaces/newlines
        cedict_regex = re.compile(r'^(\S+)\s+(\S+)\s+\[(.*?)\]\s+\/(.*)\/\s*$')
        
        if os.path.exists(CEDICT_FILE):
            with open(CEDICT_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    match = cedict_regex.match(line)
                    if match:
                        trad, simp, pinyin, meanings = match.groups()
                        defs = [m.strip() for m in meanings.split('/') if m.strip()]
                        
                        entry = {
                            "id": unified_id,
                            "type": "zh-en", # Identifies source
                            "zh_trad": trad,
                            "zh_simp": simp,
                            "pinyin": pinyin,
                            "en": defs[0] if defs else trad,
                            "defs": defs,
                            "phonetic": "",
                            "zh_trans": [],
                            "exchange": ""
                        }
                        # Write as a single line of JSON
                        out_f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                        unified_id += 1
            print("✅ CC-CEDICT parsing complete.")
        else:
            print(f"❌ Warning: {CEDICT_FILE} not found. Skipping.")


        # ==========================================
        # 2. PARSE ECDICT (English -> Chinese)
        # ==========================================
        print(f"Parsing {ECDICT_FILE}...")
        if os.path.exists(ECDICT_FILE):
            with open(ECDICT_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None) # Skip the header row
                
                for row in reader:
                    if len(row) < 4:
                        continue
                        
                    word = row[0].strip()
                    phonetic = row[1].strip()
                    def_en = row[2].strip()
                    trans_zh = row[3].strip()
                    exchange = row[10].strip() if len(row) > 10 else ""
                    
                    # Skip words that don't have a Chinese translation to save space
                    if not trans_zh:
                        continue 
                        
                    # ECDICT uses \n literals for line breaks. Convert to a clean list.
                    zh_trans_list = [t.strip() for t in trans_zh.replace('\\n', '\n').split('\n') if t.strip()]
                    
                    entry = {
                        "id": unified_id,
                        "type": "en-zh", # Identifies source
                        "zh_trad": "",
                        "zh_simp": "",
                        "pinyin": "",
                        "en": word,
                        "defs": [def_en] if def_en else [],
                        "phonetic": phonetic,
                        "zh_trans": zh_trans_list,
                        "exchange": exchange
                    }
                    out_f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    unified_id += 1
            print("✅ ECDICT parsing complete.")
        else:
            print(f"❌ Warning: {ECDICT_FILE} not found. Skipping.")

    print(f"🎉 Done! Unified dictionary saved to {OUTPUT_FILE} with {unified_id - 1} total entries.")

if __name__ == "__main__":
    build_unified_dictionary()