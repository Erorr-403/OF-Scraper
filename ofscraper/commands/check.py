import logging
import re
import time
import asyncio
import threading
import queue
import textwrap
import httpx
import arrow
import ofscraper.utils.args as args_
import ofscraper.db.operations as operations
import ofscraper.api.profile as profile
import ofscraper.utils.auth as auth
import ofscraper.api.timeline as timeline
import ofscraper.api.messages as messages_
import ofscraper.api.posts as posts_
import ofscraper.constants as constants
import ofscraper.api.paid as paid_
import ofscraper.api.archive as archive
import ofscraper.api.pinned as pinned
import ofscraper.api.highlights as highlights_
import ofscraper.utils.console as console_
import ofscraper.utils.table as table
import ofscraper.commands.manual as manual
import ofscraper.utils.download as download
import ofscraper.db.operations as operations
import ofscraper.constants as constants

from diskcache import Cache
from ..utils.paths import getcachepath
cache = Cache(getcachepath())

log = logging.getLogger(__package__)
args = args_.getargs()
console=console_.shared_console
ROW_NAMES = "Number","Download_Cart", "UserName", "Downloaded", "Unlocked", "Times_Detected", "Length", "Mediatype", "Post_Date", "Post_Media_Count", "Responsetype", "Price", "Post_ID", "Media_ID", "Text"
ROWS = []
app=None


def process_download_cart():
        while True:
            global app
            while app and not app.row_queue.empty():
                log.info("Getting items from queue")
                try:
                    row,key=app.row_queue.get() 
                    restype=app.row_names.index('Responsetype')
                    username=app.row_names.index('UserName')
                    post_id=app.row_names.index('Post_ID')
                    media_id=app.row_names.index('Media_ID')
                    url=None
                    if row[restype].plain=="message":
                        url=constants.messagesNextEP.format(row[username].plain,row[post_id].plain)
                    elif row[restype].plain=="post":
                        url=f"{row[post_id]}"
                    elif row[restype].plain=="highlights":
                        url=constants.highlightsWithStoriesEP.format(row[post_id].plain)
                    elif row[restype].plain=="stories":
                        url=constants.highlightsWithAStoryEP.format(row[post_id].plain)
                    else:
                        log.info("URL not supported")
                        continue
                    log.info(f"Added url {url}")
                    log.info("Sending URLs to OF-Scraper")
                    media_dict= manual.get_media_from_urls(urls=[url])
                    # None for stories and highlights
                    medialist=list(filter(lambda x: x.id==(int(row[media_id].plain) if x.id else None) ,list(media_dict.values())[0]))
                    media=medialist[0]
                    model_id =media.post.model_id
                    username=media.post.username
                    log.info(f"Downloading Invidual media for {username} {media.filename}")
                    operations.create_tables(model_id,username)
                    operations.write_profile_table(model_id,username)
                    values=asyncio.run(download.process_dicts(
                    username,
                    model_id,
                    [media],
                    ))
                    if values==None or values[0]!=1:
                        raise Exception("Download is marked as skipped")
                    log.info("Download Finished")
                    app.update_cell(key,"Download_Cart","[downloaded]")
                    app.update_cell(key,"Downloaded",True)

                except Exception as E:
                        app.update_downloadcart_cell(key,"[failed]")
                        log.debug(E)     
            time.sleep(10)









def post_checker():
    user_dict = {}
    with httpx.Client(http2=True, headers=  auth.make_headers(auth.read_auth())) as c:
        links = list(url_helper())
        for ele in links:
            name_match = re.search(f"onlyfans.com/({constants.USERNAME_REGEX}+$)", ele)
            name_match2 = re.search(f"^{constants.USERNAME_REGEX}+$", ele)

            if name_match:
                user_name = name_match.group(1)
                log.info(f"Getting Full Timeline for {user_name}")
                model_id = profile.get_id( user_name)
            elif name_match2:
                user_name = name_match2.group(0)
                model_id = profile.get_id( user_name)
            else:
                continue
        
            oldtimeline = cache.get(f"timeline_check_{model_id}", default=[])
            if len(oldtimeline) > 0 and not args.force:
                user_dict[user_name] = oldtimeline
            elif not user_dict.get(user_name):
                user_dict[user_name] = {}
                user_dict[user_name] = user_dict[user_name] or []
                user_dict[user_name].extend(asyncio.run(
                    timeline.get_timeline_post( model_id)))
                user_dict[user_name].extend(asyncio.run(
                    pinned.get_pinned_post( model_id)))
                user_dict[user_name].extend(asyncio.run(
                    archive.get_archived_post( model_id)))
                cache.set(
                    f"timeline_check_{model_id}", user_dict[user_name], expire=constants.CHECK_EXPIRY)
                cache.close()
                

        # individual links
        for ele in list(filter(lambda x: re.search(f"onlyfans.com/{constants.NUMBER_REGEX}+/{constants.USERNAME_REGEX}+$", x), links)):
            name_match = re.search(f"/({constants.USERNAME_REGEX}+$)", ele)
            num_match = re.search(f"/({constants.NUMBER_REGEX}+)", ele)
            if name_match and num_match:
                user_name = name_match.group(1)
                post_id=num_match.group(1)
                model_id = profile.get_id( user_name)
                log.info(f"Getting Invidiual Link for {user_name}")
                if not user_dict.get(user_name):
                    user_dict[name_match.group(1)] = {}
                data = timeline.get_individual_post(post_id, c)
                user_dict[user_name] = user_dict[user_name] or []
                user_dict[user_name].append(data)

    ROWS=[]
    for user_name in user_dict.keys():
        downloaded = get_downloaded(user_name, model_id,True)
        media = get_all_found_media(user_name, user_dict[user_name])
        ROWS.extend(row_gather(media, downloaded, user_name))
    reset_url() 
    set_count(ROWS)
    thread_starters(ROWS)

def reset_url():
    #clean up args once check modes are ready to launch
    args=args_.getargs()
    argdict=vars(args)
    if argdict.get("url"):
        args.url=None
    if argdict.get("file"):
        args.file=None
    if argdict.get("username"):
        args.username=None
    args_.changeargs(args)
    



def set_count(ROWS):
    for count,ele in enumerate(ROWS):
        ele[0]=count+1


def message_checker():
    links = list(url_helper())
    ROWS=[]
    for item in links:
        num_match = re.search(f"({constants.NUMBER_REGEX}+)", item) or re.search(f"^({constants.NUMBER_REGEX}+)$", item)
        name_match = re.search(f"^{constants.USERNAME_REGEX}+$", item)
        headers = auth.make_headers(auth.read_auth())
        if num_match:
            model_id = num_match.group(1)
            user_name = profile.scrape_profile( model_id)['username']
        elif name_match:
            user_name = name_match.group(0)
            model_id = profile.get_id(user_name) 
        else:
            continue    
        log.info(f"Getting Messages/Paid content for {user_name}")
        # messages
        messages = None
        oldmessages = cache.get(f"message_check_{model_id}", default=[])
        log.debug(f"Number of messages in cache {len(oldmessages)}")
        
        if len(oldmessages) > 0 and not args.force:
            messages = oldmessages
        else:
            messages = asyncio.run(
                messages_.get_messages(headers,  model_id))
            cache.set(f"message_check_{model_id}",
                        messages, expire=constants.CHECK_EXPIRY)
        oldpaid = cache.get(f"purchased_check_{model_id}", default=[])
        paid = None
        # paid content
        if len(oldpaid) > 0 and not args.force:
            paid = oldpaid
        else:
            paid = asyncio.run(paid_.get_paid_posts(user_name, model_id))
            cache.set(f"purchased_check_{model_id}",
                      paid, expire=constants.CHECK_EXPIRY)  
        media = get_all_found_media(user_name, messages+paid)
        unduped=[]
        id_set=set()
        for ele in media:
            if ele.id==None or ele.id not in id_set:
                unduped.append(ele)
                id_set.add(ele.id)
        downloaded = get_downloaded(user_name, model_id,True)
        ROWS.extend(row_gather(unduped, downloaded, user_name))
    
    reset_url()
    set_count(ROWS)
    thread_starters(ROWS)



def purchase_checker():
    user_dict = {}
    headers = auth.make_headers(auth.read_auth())
    ROWS = []
    for user_name in args.username:
        user_name=profile.scrape_profile(user_name)["username"]
        user_dict[user_name] = user_dict.get(user_name, [])
        model_id = profile.get_id( user_name)
        oldpaid = cache.get(f"purchased_check_{model_id}", default=[])
        paid = None
        
        if len(oldpaid) > 0 and not args.force:
            paid = oldpaid
        else:
            paid = asyncio.run(paid_.get_paid_posts(user_name, model_id))
            cache.set(f"purchased_check_{model_id}",
                      paid, expire=constants.CHECK_EXPIRY)
        downloaded = get_downloaded(user_name, model_id)
        media = get_all_found_media(user_name, paid)
        ROWS.extend(row_gather(media, downloaded, user_name))
    reset_url()
    set_count(ROWS)
    thread_starters(ROWS)


def stories_checker():
    user_dict = {}
    ROWS=[]
    for user_name in args.username:
        user_name=profile.scrape_profile(user_name)["username"]
        user_dict[user_name] = user_dict.get(user_name, [])
        model_id = profile.get_id( user_name)    
        highlights, stories = asyncio.run(highlights_.get_highlight_post( model_id))
        highlights=list(map(lambda x:posts_.Post(
        x, model_id, user_name,"highlights"), highlights))
        stories=list(map(lambda x:posts_.Post(
        x, model_id, user_name,"stories"), stories))
            

     
        downloaded = get_downloaded(user_name, model_id)
        media=[]
        [media.extend(ele.all_media) for ele in stories+highlights]
        ROWS.extend(row_gather(media, downloaded, user_name))
    reset_url()
    set_count(ROWS)
    thread_starters(ROWS)

  


def url_helper():
    out = []
    out.extend(args.file or [])
    out.extend(args.url or [])
    return map(lambda x: x.strip(), out)


def get_all_found_media(user_name, posts):

    temp = []
    model_id = profile.get_id( user_name)
    posts_array=list(map(lambda x:posts_.Post(
        x, model_id, user_name), posts))
    [temp.extend(ele.all_media) for ele in posts_array]
    return temp




def get_downloaded(user_name, model_id,paid=False):
    downloaded = {}
    operations.create_tables(model_id, user_name)
    paid=get_paid_ids(model_id,user_name) if paid else []
    [downloaded.update({ele: downloaded.get(ele, 0)+1}) for ele in operations.get_media_ids(model_id, user_name)+paid]

    return downloaded

def get_paid_ids(model_id,user_name):
    oldpaid = cache.get(f"purchased_check_{model_id}", default=[])
    paid = None
        
    if len(oldpaid) > 0 and not args.force:
         paid = oldpaid
    else:
        paid = asyncio.run(paid_.get_paid_posts(user_name, model_id))
        cache.set(f"purchased_check_{model_id}",
                      paid, expire=constants.CHECK_EXPIRY)
    media = get_all_found_media(user_name, paid)
    media=list(filter(lambda x:x.canview==True,media))
    return list(map(lambda x:x.id,media))


def thread_starters(ROWS_): 
    worker_thread = threading.Thread(target=process_download_cart)
    worker_thread.start()
    start_table(ROWS_)
    

def start_table(ROWS_):
    global app
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app=table.InputApp()
    app.mutex=threading.Lock()
    app.row_queue=queue.Queue()
    ROWS = get_first_row()
    ROWS.extend(ROWS_)
   
    app.table_data = ROWS
    app.row_names = ROW_NAMES
    app._filtered_rows = app.table_data[1:]
    app.run()





def get_first_row():
    return [ROW_NAMES]


def texthelper(text):
    text=text or ""
    text = textwrap.dedent(text)
    text = re.sub(" +$", "", text)
    text = re.sub("^ +", "", text)
    text = re.sub("<[^>]*>", "", text)
    text = text if len(
        text) < constants.TABLE_STR_MAX else f"{text[:constants.TABLE_STR_MAX]}..."
    return text


def unlocked_helper(ele):
    return ele.canview


def datehelper(date):
    if date == "None":
        return "Probably Deleted"
    return date


def times_helper(ele, mediadict, downloaded):
    return max(len(mediadict.get(ele.id, [])), downloaded.get(ele.id, 0))

def checkmarkhelper(ele):
    return '[]' if unlocked_helper(ele) else "Not Unlocked"
  
def row_gather(media, downloaded, username):

    # fix text

    mediadict = {}
    [mediadict.update({ele.id: mediadict.get(ele.id, []) + [ele]})
     for ele in list(filter(lambda x:x.canview,media))]
    out = []
    media = sorted(media, key=lambda x: arrow.get(x.date), reverse=True)
    for count, ele in enumerate(media):
        out.append([None,checkmarkhelper(ele),username, ele.id in downloaded or cache.get(ele.postid)!=None or  cache.get(ele.filename)!=None , unlocked_helper(ele), times_helper(ele, mediadict, downloaded), ele.length_, ele.mediatype, datehelper(
            ele.postdate_), len(ele._post.post_media), ele.responsetype_, "Free" if ele._post.price == 0 else "{:.2f}".format(ele._post.price),  ele.postid, ele.id, texthelper(ele.text)])
    return out

