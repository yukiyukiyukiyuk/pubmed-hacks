import requests
import math
import urllib.parse
import xml.etree.ElementTree as ET
import datetime
from paper_abstract_app.models import Journal, Articlemodel
from fuzzywuzzy import process,fuzz

###################################################################################################################
# 各自入力が必要な項目
# pubmed search parameters
SOURCE_DB    = 'pubmed'
DATE_TYPE    = 'pdat'       # Type of date used to limit a search. The allowed values vary between Entrez databases, but common values are 'mdat' (modification date), 'pdat' (publication date) and 'edat' (Entrez date). Generally an Entrez database will have only two allowed values for datetype.
SEP          = ', '
BATCH_NUM    = 1000

## deepL ##
# API key
deepl_key = "551b413a-250b-578d-b50e-2771515c0c13:fx"
# API URL
deepl_path = "https://api-free.deepl.com/v2/translate"
# 有料の場合は
# deepl_path = "https://api.deepl.com/v2/translate"
###################################################################################################################

# pubmed api URL
BASEURL_INFO = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi'
BASEURL_SRCH = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi' #Esearch: キーワードと期間からPMIDを取得する
BASEURL_FTCH = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
journal_list = [journal.name for journal in Journal.objects.all()]

def extractOne(query, choices, scorer=fuzz.WRatio, processor=lambda x: x, score_cutoff=0):
    # Generate tuples of the form (choice, score, index)
    scores = [(choice, scorer(processor(query), processor(choice)), index) for index, choice in enumerate(choices)]
    scores = [t for t in scores if t[1] >= score_cutoff]

    if not scores:
        return None, None, None

    # Return the best match, its score, and its index
    return max(scores, key=lambda t: t[1])

def match_journal(journal_name, journal_list, threshold=85):
    # Normalize the input
    journal_name = journal_name.lower()
    # Create a normalized version of the journal list
    normalized_journal_list = [j.lower() for j in journal_list]

    # Check for an exact match in the normalized list
    exact_match_index = next((index for index, j in enumerate(normalized_journal_list) if j == journal_name), None)
    if exact_match_index is not None:
        print('完全一致')
        # Return the corresponding entry from the original list
        return journal_list[exact_match_index]

    # If there's no exact match, find the best fuzzy match in the normalized list
    best_match, score, best_match_index = extractOne(journal_name, normalized_journal_list)
    print(score)
    # If the score exceeds the threshold, return the match from the original list. Otherwise, return None
    return journal_list[best_match_index] if score >= threshold else None

def mkquery(base_url, params):
    base_url += '?'
    for key, value in zip(params.keys(), params.values()):
        base_url += '{key}={value}&'.format(key=key, value=value)
    url = base_url[0:len(base_url) - 1]
    print('request url is: ' + url)
    return url


def getXmlFromURL(base_url, params):
    response = requests.get(mkquery(base_url, params))
    root = ET.ElementTree(ET.fromstring(response.content)).getroot()
    return root


def getTextFromNode(root, path, fill='', mode=0, attrib='attribute'):
    if (root.find(path) == None):
        return fill
    else:
        if mode == 0:
            return root.find(path).text
        if mode == 1:
            return root.find(path).get(attrib)


def deepl_translate(text):
   # URLクエリに仕込むパラメータの辞書を作っておく
   params = {
               "auth_key": deepl_key,
               "text": text,
               "source_lang": 'EN', # 入力テキストの言語を日本語に設定（JPではなくJAなので注意）
               "target_lang": 'JA'  # 出力テキストの言語を英語に設定
           }
   # パラメータと一緒にPOSTする
   request = requests.post(deepl_path, data=params)
   result = request.json()

   # 翻訳後のテキストで一意な文字列を元のコロンに戻す
   for translation in result['translations']:
       translation['text'] = translation['text'].replace('__UNIQUE_COLON__', ':')

   return result

def get_all_text(element):
    text = element.text or ""
    for child in element:
        text += get_all_text(child)
        if child.tail:
            text += child.tail
    return text


def get_abstract_sections(article):
    abstract_sections = article.findall('MedlineCitation/Article/Abstract/AbstractText')
    abstract_list = []
    
    for section in abstract_sections:
        label = section.get('Label')
        category = section.get('NlmCategory')
        text = get_all_text(section) if section.text is not None else ""
        
        if label :
            abstract_list.append(f"{label}  __UNIQUE_COLON__  {text}") # コロンを一意な文字列に置き換え
        elif category:
            abstract_list.append(f"{category}  __UNIQUE_COLON__  {text}") # コロンを一意な文字列に置き換え
        else:
            abstract_list.append(text)

    return "\n".join(abstract_list)

def convert_newlines_to_html(abstract_list):
    for abstract in abstract_list['translations']:
        lines = abstract['text'].split('\n')
        formatted_lines = []

        for line in lines:
            if ':' in line:
                label, content = line.split(':', 1)
                formatted_line = f'{label}:{content}'
            else:
                formatted_line = line

            formatted_lines.append(formatted_line)

        abstract['text'] = '<br>'.join(formatted_lines)

    return abstract_list

def getArticle(TERM,MIN_DATE,MAX_DATE,NUM=None):
    # get xml
    rootXml = getXmlFromURL(BASEURL_SRCH, {
        'db': SOURCE_DB,
        'term': TERM,
        'usehistory': 'y',
        'datetype': DATE_TYPE,
        'mindate': MIN_DATE,
        'maxdate': MAX_DATE})
    # get querykey and webenv
    Count = rootXml.find('Count').text
    QueryKey = rootXml.find('QueryKey').text
    WebEnv = urllib.parse.quote(rootXml.find('WebEnv').text)

    print('total Count: ', Count)
    print('QueryKey   : ', QueryKey)
    print('WebEnv     : ', WebEnv)

    PMID_list = []
    Date_pubmed_list = []
    Title_list = []
    Author_list = []
    Abstract_list = []
    JournalTitle_list = []
    doi_list = []

    iterCount = math.ceil(int(Count) / int(BATCH_NUM)) if NUM is None else math.ceil(int(NUM) / int(BATCH_NUM))

    for i in range(iterCount):
        rootXml = getXmlFromURL(BASEURL_FTCH, {
            'db': SOURCE_DB,
            'query_key': QueryKey,
            'WebEnv': WebEnv,
            'retstart': i * BATCH_NUM,
            'retmax': BATCH_NUM,
            'retmode': 'xml'})
        
        for article in rootXml.iter('PubmedArticle'):
            # get published date
            year_p = getTextFromNode(article, 'MedlineCitation/Article/Journal/JournalIssue/PubDate/Year', '')
            month_p = getTextFromNode(article, 'MedlineCitation/Article/Journal/JournalIssue/PubDate/Season', '') if getTextFromNode(article, 'MedlineCitation/Article/Journal/JournalIssue/PubDate/Season', '') else getTextFromNode(article, 'MedlineCitation/Article/Journal/JournalIssue/PubDate/Month', '')
            date_p = str(year_p) + "-" + str(month_p)
            
            # get article info
            year_a = getTextFromNode(article, 'MedlineCitation/Article/ArticleDate/Year', '')
            month_a = getTextFromNode(article, 'MedlineCitation/Article/ArticleDate/Month', '')
            day_a = getTextFromNode(article, 'MedlineCitation/Article/ArticleDate/Day', '')
            try:
                date_a = datetime.date(int(year_a), int(month_a), int(day_a)).strftime('%Y-%m-%d')
                
            except:
                date_a = ""
                

            year_pm = getTextFromNode(article, 'PubmedData/History/PubMedPubDate[@PubStatus="pubmed"]/Year', ''),
            month_pm = getTextFromNode(article, 'PubmedData/History/PubMedPubDate[@PubStatus="pubmed"]/Month', ''),
            day_pm =  getTextFromNode(article, 'PubmedData/History/PubMedPubDate[@PubStatus="pubmed"]/Day', ''),
            try:
                date_pm = datetime.date(int(year_pm[0]), int(month_pm[0]), int(day_pm[0])).strftime('%Y-%m-%d')

            except:
                date_pm = ""
            
            Title = get_all_text(article.find('MedlineCitation/Article/ArticleTitle'))
            Abstract = get_abstract_sections(article)
            JournalTitle = getTextFromNode(article, 'MedlineCitation/Article/Journal/Title', '')
            matched_journal = match_journal(JournalTitle, journal_list)

            if matched_journal is None:
                Journal.objects.create(name=JournalTitle)
                matched_journal = JournalTitle

            print(JournalTitle)              
            Date_publish = date_p
            Date_article = date_a
            Date_pubmed = date_pm
            Status = getTextFromNode(article, './PubmedData/PublicationStatus', '')
            Keyword = SEP.join([keyword.text if keyword.text != None else ''  for keyword in article.findall('MedlineCitation/KeywordList/')])
            MeSH = SEP.join([getTextFromNode(mesh, 'DescriptorName') for mesh in article.findall('MedlineCitation/MeshHeadingList/')])
            MeSH_UI = SEP.join([getTextFromNode(mesh, 'DescriptorName', '', 1, 'UI') for mesh in article.findall('MedlineCitation/MeshHeadingList/')])        
            PMID = getTextFromNode(article, 'MedlineCitation/PMID', '') 
            Authors = SEP.join([author.find('ForeName').text + ' ' +  author.find('LastName').text if author.find('CollectiveName') == None else author.find('CollectiveName').text for author in article.findall('MedlineCitation/Article/AuthorList/')])         
            doi = getTextFromNode(article, 'MedlineCitation/Article/ELocationID[@EIdType="doi"]', '')
            Language = getTextFromNode(article, 'MedlineCitation/Article/Language', '')

            PMID_list.append(PMID)
            Date_pubmed_list.append(Date_publish)
            Title_list.append(Title)
            Author_list.append(Authors)
            Abstract_list.append(Abstract)
            JournalTitle_list.append(matched_journal)
            doi_list.append(doi)

    Title_list_translated = deepl_translate(Title_list)
    Abstract_list_translated = convert_newlines_to_html(deepl_translate(Abstract_list))
  
    AbstractModel_list = []

    for i in range(len(Title_list)):

        Title_translated = Title_list_translated['translations'][i]['text']
        Abstract_translated = Abstract_list_translated['translations'][i]['text']
        JournalTitle_p = JournalTitle_list[i]
        Date_pubmed_p = Date_pubmed_list[i]
        print(Date_pubmed_p)
        PMID_p = PMID_list[i]
        Author_p = Author_list[i]
        doi_p = 'https://doi.org/' + doi_list[i]

        journal, _ = Journal.objects.get_or_create(name=JournalTitle_p)
        

        data = Articlemodel()
        data.PMID = PMID_p
        data.Date_publish = Date_pubmed_p
        data.Title = Title_translated
        data.Author = Author_p
        data.Abstract = Abstract_translated
        data.journal = journal
        data.DOI = doi_p
        AbstractModel_list.append(data)
        
    return AbstractModel_list