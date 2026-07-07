"""
This module provides functions for extracting and normalizing date information from HTML content.
"""
import re
from datetime import datetime
from bs4 import BeautifulSoup, Tag

# 預編譯正則表達式以提升效能
_COMPILED_KEYWORD_PATTERNS = [
    re.compile(r'(?:更新日期|發布日期|修改日期|上版日期|上架日期|發佈日期|建檔日期|最後更新|資料更新|內容更新|資料檢視|Data update|Review Date)[:：\s]*(\d{2,4})(?:年|[/\-\.])(\d{1,2})(?:月|[/\-\.])(\d{1,2})(?:[日號])?'),
    re.compile(r'(?:更新日期|發布日期|修改日期|上版日期|上架日期|發佈日期|建檔日期|最後更新|資料更新|內容更新|資料檢視|Data update|Review Date)[:：\s]*(\d{2,4})(?:年|[/\-\.])(\d{1,2})月?(?![/\-\.]\d)(?!\d)'),
    re.compile(r'(\d{2,4})(?:年|[/\-\.])(\d{1,2})(?:月|[/\-\.])(\d{1,2})(?:[日號])?\s*(?:更新|發布|修改|發佈)'),
    re.compile(r'(\d{2,4})(?:年|[/\-\.])(\d{1,2})月?(?![/\-\.])\s*(?:更新|發布|修改|發佈)')
]

_COMPILED_GENERIC_PATTERNS = [
    re.compile(r"(?<![\d+*/=.:;@#$%^&|\\])(\d{2,4})(?:年|[/\-\.])(\d{1,2})(?:月|[/\-\.])(\d{1,2})(?:[日號])?(?!\d)"),
    re.compile(r"(?<![\d~\-+*/=.:;@#$%^&|\\])(\d{2,4})(?:年|[/\-\.])(0[1-9]|1[0-2]|[1-9])(?![/\.\月]\d)(?!\d|°)"),
    re.compile(r"(?<![\d+*/=.:;@#$%^&|\\])(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})"),
    re.compile(r"(?<![\d+*/=.:;@#$%^&|\\])(\d{1,2})[/\-\.]((?:19|20)\d{2})(?!\d)")
]

def _clean_html_noise(soup: BeautifulSoup) -> BeautifulSoup:
    """
    移除HTML中的雜訊元素，返回清理後的HTML
    """
    cleaned_soup = BeautifulSoup(str(soup), 'html.parser')
    
    # 要移除的標籤類型
    noise_tags = ['header', 'nav', 'aside', 'footer']
    
    # 移除雜訊標籤
    for tag_name in noise_tags:
        for tag in cleaned_soup.find_all(tag_name):
            tag.decompose()
    
    # 要移除的CSS類名模式
    noise_class_patterns = [
        'base-footer', 'site-footer', 'footer-container', 'footer-wrapper', 
        'footer-bottom', 'site-info', 'colophon', 'copyright', 'update-time', 
        'visit-count', 'nav', 'navigation', 'navbar', 'nav-menu', 'main-nav', 
        'site-nav', 'breadcrumb', 'sidebar', 'menu', 'top-menu'
    ]
    
    # 移除具有雜訊類名的元素
    elements_to_remove = []  # 先收集要移除的元素
    for element in cleaned_soup.find_all(attrs={'class': True}):
        if element is None:  # 防止 None 元素
            continue
            
        class_names = element.get('class', [])
        if isinstance(class_names, str):
            class_names = [class_names]
        
        class_str = ' '.join(class_names).lower()
        
        # 檢查是否包含任何雜訊類名
        for pattern in noise_class_patterns:
            if pattern in class_str:
                elements_to_remove.append(element)
                break
    
    # 批量移除元素以避免迭代過程中修改DOM的問題
    for element in elements_to_remove:
        if element and element.parent:  # 確保元素仍存在且有父元素
            element.decompose()
    
    return cleaned_soup

def _normalize_date_string(groups: tuple) -> str:
    """
    根據正則表達式匹配的 groups 正規化日期格式為 'YYYY-MM-DD'
    Args:
        groups: 正則表達式匹配的分組元組
    Returns:
        標準化的日期字符串 'YYYY-MM-DD'，如果日期太舊則返回空字符串
    """
    if not groups:
        return ""
    
    # 轉換為整數列表，過濾空值
    nums = [int(g) for g in groups if g and g.isdigit()]
    
    if len(nums) == 3:
        # 三個數字：年月日
        a, b, c = nums
        
        # 判斷是否為日月年格式（最後一個是四位數西元年）
        if c >= 1900:
            # 日月年格式
            day, month, year = a, b, c
            # 西元年必須在1990年以後
            if year < 1990:
                return ""
        else:
            # 年月日格式
            year, month, day = a, b, c
            # 如果年份小於200，視為民國年
            if year < 200:
                # 民國年必須在79年以後（對應西元1990年）
                if year < 79:
                    return ""
                year += 1911
            else:
                # 西元年必須在1990年以後
                if year < 1990:
                    return ""
        
        return f"{year:04d}-{month:02d}-{day:02d}"
    
    elif len(nums) == 2:
        # 兩個數字：年月
        a, b = nums
        
        # 判斷是否為月年格式（最後一個是四位數西元年）
        if b >= 1900:
            # 月年格式
            month, year = a, b
            # 西元年必須在1990年以後
            if year < 1990:
                return ""
        else:
            # 年月格式
            year, month = a, b
            # 如果年份小於200，視為民國年
            if year < 200:
                # 民國年必須在79年以後（對應西元1990年）
                if year < 79:
                    return ""
                year += 1911
            else:
                # 西元年必須在1990年以後
                if year < 1990:
                    return ""
        
        # 預設為該月第一天
        return f"{year:04d}-{month:02d}-01"
    
    # 無法解析
    return ""

def _search_for_date_in_scope(scope: Tag, scope_name: str = "unknown", log_func=None) -> tuple[list[str], bool]:
    """Searches for dates within a specific BeautifulSoup scope (tag).
    Returns a tuple of (found_dates, used_generic_patterns)."""
    if not scope:
        return [], False

    found_dates = []
    used_generic_patterns = False

    def _log(message):
        if log_func:
            log_func(message)
        else:
            print(message)

    keyword_matches = []  # 關鍵詞模式的匹配結果
    generic_matches = []  # 通用格式的匹配結果
    
    # 批量提取所有文本元素，避免重複遍歷DOM
    text_elements = []
    for element in scope.find_all(string=True):
        if element.parent and element.strip():
            text_elements.append(element.strip())
    
    for text_content in text_elements:
        # 收集關鍵詞模式匹配
        for i, compiled_pattern in enumerate(_COMPILED_KEYWORD_PATTERNS):
            matches = compiled_pattern.finditer(text_content)
            for match in matches:
                # 傳入所有捕獲的分組（排除第0組完整匹配）
                date_groups = match.groups()
                date_str = _normalize_date_string(date_groups)
                if date_str and date_str not in found_dates:
                    keyword_matches.append((i+1, date_str, str(date_groups), match.group(0)))
        
        # 收集通用格式匹配
        for i, compiled_pattern in enumerate(_COMPILED_GENERIC_PATTERNS):
            matches = compiled_pattern.finditer(text_content)
            for match in matches:
                # 傳入所有捕獲的分組（排除第0組完整匹配）
                date_groups = match.groups()
                date_str = _normalize_date_string(date_groups)
                if date_str and date_str not in found_dates:
                    generic_matches.append((i+1, date_str, str(date_groups)))
    
    # 根據優先級處理結果
    if keyword_matches:
        # 有關鍵詞匹配，使用關鍵詞結果
        for pattern_num, date_str, original, full_match in keyword_matches:
            if date_str not in found_dates:
                _log(f"🎯 找到日期: {date_str} (來源: 關鍵詞, 原始: {original})")
                found_dates.append(date_str)
    else:
        # 沒有關鍵詞匹配，使用通用格式結果，並標記已使用通用格式
        used_generic_patterns = True
        if generic_matches:
            for pattern_num, date_str, original in generic_matches:
                if date_str not in found_dates:
                    _log(f"📅 找到日期: {date_str} (來源: 通用格式, 原始: {original})")
                    found_dates.append(date_str)
                            
    return found_dates, used_generic_patterns

def _select_best_date(dates: list[str], log_func=None) -> str:
    """
    從多個日期中選擇最合適的一個作為網站最後更新日期
    策略：
    1. 優先選擇最近的日期（通常是最後更新日期）
    2. 排除未來日期
    3. 如果沒有找到任何日期，返回 "[無日期]"
    """
    
    def _log(message):
        if log_func:
            log_func(message)
        else:
            print(message)
    
    if not dates:
        _log(f"  ❌ 無法找到有效日期，返回無日期")
        return "[無日期]"
    
    if len(dates) == 1:
        _log(f"  ✅ 只有一個日期，直接選擇: {dates[0]}")
        return dates[0]
    
    # 過濾和排序日期，同時記錄最接近的日期
    valid_dates = []
    closest_date = None
    closest_diff = None
    current_obj = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    for date_str in dates:
        try:
            # 檢查日期格式
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                date_obj = datetime.strptime(date_str, "%Y-%m-%d") 
                
                # 計算與當前日期的時間差，記錄最接近的日期
                diff = abs((date_obj - current_obj).days)
                if closest_diff is None or diff < closest_diff:
                    closest_diff = diff
                    closest_date = date_str
                
                # 排除未來日期和當天日期
                if date_obj >= current_obj:
                    continue
                
                valid_dates.append(date_str)
        except ValueError:
            continue
    
    if not valid_dates:
        if closest_date:
            return closest_date
        else:
            return "[無日期]"
    
    # 返回最近的日期
    best_date = max(valid_dates)
    _log(f" 🏆 最終選擇的日期: {best_date}")
    return best_date


def extract_last_updated(soup: BeautifulSoup, log_func=None) -> str:
    """
    Extracts the last updated date from a BeautifulSoup object using a hierarchical and semantic strategy.
    當網頁有多個日期時，會選擇最合適的一個。
    
    1. 首先清理HTML中的雜訊元素（header, footer, nav等）
    2. 在清理後的HTML中搜尋日期
    3. 如果使用了通用格式模式，則檢查meta標籤作為補充
    """
    def _log(message):
        if log_func:
            log_func(message)
        else:
            print(message)
    
    all_found_dates = []
    used_generic_patterns = False
    
    # 清理HTML雜訊
    cleaned_soup = _clean_html_noise(soup)
    
    # 先嘗試在清理後的 body 中搜尋
    cleaned_body = cleaned_soup.find('body')
    if cleaned_body:
        # 在清理後的 body 中搜尋日期
        scope_dates, scope_used_generic = _search_for_date_in_scope(cleaned_body, 'cleaned body', log_func)
        if scope_used_generic:
            used_generic_patterns = True
        for date in scope_dates:
            if date and date not in all_found_dates:
                all_found_dates.append(date)
    else:
        # 如果沒有找到 body，則在整個清理後的文檔中搜尋
        scope_dates, scope_used_generic = _search_for_date_in_scope(cleaned_soup, 'cleaned entire document', log_func)
        if scope_used_generic:
            used_generic_patterns = True
        for date in scope_dates:
            if date and date not in all_found_dates:
                all_found_dates.append(date)

    # 2. Check meta tags only if generic patterns were used
    if used_generic_patterns:
        meta_properties = [
            'og:article:modified_time', 'og:modified_time', 'article:modified_time',
            'og:article:published_time', 'og:published_time', 'article:published_time',
            'DC.date.modified', 'dcterms.modified', 'DC.Date', 'dcterms.created',
            'DC.Coverage.t.min', 'DC.Coverage.t.max'
        ]
        for prop in meta_properties:
            meta_tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if meta_tag and meta_tag.get('content'):
                content = meta_tag['content'].strip()
                # 直接用 - 分割處理 YYYY-MM-DD 或 YYYY-MM 格式
                parts = content.split('-')
                if len(parts) >= 2:  # 至少有年月
                    # 檢查前兩個部分是否為數字且年份為4位數
                    if (len(parts[0]) == 4 and parts[0].isdigit() and 
                        parts[1].isdigit()):
                        date_groups = tuple(parts[:3])  # 最多取前3個部分（年月日）
                        date_str = _normalize_date_string(date_groups)
                        if date_str and date_str not in all_found_dates:
                            _log(f"🏷️ 找到日期: {date_str} (來源: meta標籤, 原始: {content})")
                            all_found_dates.append(date_str)
    
    # Select the best date from all found dates
    result = _select_best_date(all_found_dates, log_func)
    return result
