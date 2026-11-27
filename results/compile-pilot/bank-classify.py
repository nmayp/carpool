import re

def _has(t, pat):
    return re.search(pat, t) is not None

PURCHASE = r"\b(purchase[ds]?|bought|buy|payment|item|paid|card|abroad|holiday|overseas|traveling|travelling|shop|merchant|foreign currency|another country|different currency|another currency)\b"
WRONG = (r"(wrong|incorrect|not right|n't right|isn't correct|not correct|wasn't correct|off\b|error|mistake|different|bad\b|"
         r"over ?charg|problem|issue|awful|terrible|n't look right|inaccurate|too much|extra|"
         r"charg\w* me more|(\bnot|n't) (\w+ ){0,5}(correct|right|accurate)\b)")
WRONG_EXTRA = r"(correct|right|proper) (exchange )?rate (was|is)(n't| not)?\b.*(n't|\bnot\b)|(correct|right|proper) (exchange )?rate (was|is)(n't| not)"


def _deposit(t):
    if not _has(t, r"\b(cheque|check|checque|cash|deposit(ed|s)?)\b"):
        return None
    if _has(t, r"withdraw|\batm\b.*(withdraw|declin)|card|top.?up|accident|how (do|can) i deposit|where can i deposit|limit|fee|charge|pending transfer"):
        return None
    if _has(t, r"\b(transfer|wire)\b"):
        return None
    if _has(t, r"error|fail|declin|reject|unable|couldn't|could not|\b(send|sending)\b.*\b(to|over)\b"):
        return None
    if not _has(t, r"\b(deposit|cheque|check|checque|cash|money)\b"):
        return None
    cue = _has(t, r"(n't|\bnot\b|\bno\b|\byet\b|still|when|where|missing|haven|hasn|never|nothing|how long|why|updat|show|post|reflect|credit|clear|arriv|see)")
    ctx = _has(t, r"(balance|account|deposit|cheque|check|checque)")
    if cue and ctx and _has(t, r"(deposit|cheque|check|checque|cash)"):
        if _has(t, r"\b(deposit|deposited|put|placed|made|sent|submitted|updat\w*)\b|cheque|checque|\bcheck\b"):
            return "balance_not_updated_after_cheque_or_cash_deposit"
    return None


def _exchange(t):
    if not _has(t, r"exchange|\brate\b|currency|convert|swap|ruble|rouble|russian"):
        return None
    if _has(t, r"disposable|\batm\b|withdraw|cash withdrawal|top.?up|transfer|apple pay|google pay|support|which currenc|what currenc|exchange (money|currency) in|via the app|in the app|in app|can i exchange|how do i exchange|how to exchange"):
        return None
    wrong = _has(t, WRONG) or _has(t, WRONG_EXTRA)
    purchase = _has(t, PURCHASE)
    ruble = _has(t, r"ruble|rouble|russian|\brub\b")
    fee = _has(t, r"\bfee")
    chargeword = _has(t, r"fee|charg|overcharg|too much|too high|way more|more than|less than")
    if purchase and wrong and not ruble:
        return "card_payment_wrong_exchange_rate"
    ratephrase = _has(t, r"exchange rate|currency exchange|\brate\b|currency rate")
    if (not purchase and not ruble and not fee and ratephrase
            and _has(t, r"interbank|official|actual (exchange )?rate|real (exchange )?rate")):
        return "exchange_rate"
    if ratephrase and wrong and not ruble and not fee:
        if not purchase and (_has(t, r"google|\bupdat") or
                             (_has(t, r"currency rate") and not _has(t, r"exchange"))):
            return "exchange_rate"
        return "card_payment_wrong_exchange_rate"
    if (ruble or _has(t, r"exchange")) and chargeword and not purchase and (ruble or _has(t, r"fee|exchange")):
        return "exchange_charge"
    if _has(t, r"exchange rate|currency rate|foreign exchange") and not purchase:
        if _has(t, r"what|how|where|which|need to know|know about|interbank|official|update|better|bad|terrible|awful"):
            return "exchange_rate"
    return None


def _wrong_amount(t):
    if _has(t, r"transfer|withdraw|\batm\b|cash|top.?up|\bfee|deposit|cheque|check|refund|pending"):
        return None
    if _has(t, r"\bcharged (me )?(for )?(the |a |an )?\w+ amount") and _has(t, r"\b(item|purchas\w*|bought)\b"):
        return "card_payment_wrong_exchange_rate"
    return None


def _card_declined(t):
    if _has(t, r"transfer|\bsend|sent|withdraw|\batm\b|cash|top.?up|cheque|check|deposit|exchange|\bapp\b|mortgage"):
        return None
    if _has(t, r"\b(transaction|card payment|payment)s?\b") and _has(t, r"\bdeclin\w*"):
        return "declined_card_payment"
    return None


FAILWORDS = (r"(fail|didn'?t go through|did not go through|not going through|won'?t go through|isn'?t going through|not go through|"
             r"reject|declin|error|unable|couldn'?t|could not|can'?t (seem to )?(make|complete|do)|cant (make|complete|do)|"
             r"didn'?t work|not work|doesn'?t (seem to )?work|didn'?t complete|unsuccessful|stopped|keeps|not able|wouldn'?t|not been able|"
             r"(doesn'?t|does not|isn'?t|isnt|not) (seem to be )?working|system (is |may be |might be )?(down|broken)|"
             r"tried \d+ ?(x|times)|\d ?(x|times)|going wrong|something('s| is) wrong|what('s| is) wrong|"
             r"not (been )?transferred|(hasn'?t|haven'?t|didn'?t|wasn'?t|isn'?t) (been )?transfer\w*)")


def _transfer(t):
    has_tr = _has(t, r"\btransfer(s|red|ring)?\b|\btransger\b")
    has_send = _has(t, r"\b(send|sending|sent)\b")
    if not (has_tr or has_send):
        return None
    if _has(t, r"pending|receiv|recipient|\bfee|charged|card|top.?up|top-up|topping|refund|cancel|beneficiar|revert|limit|statement|timing|how long|arrive|\bsalary\b|exchange|verify|identity"):
        return None
    if has_tr and _has(t, r"cheque|\bchecks?\b(?! (the|on|if|why|what|into|this|my|it))|cash|deposit"):
        return None
    howq = _has(t, r"how do i|how can i|how to")
    howq_block = howq and not _has(t, r"why|reason|error|wrong|find out|what'?s")
    if _has(t, FAILWORDS) and not _has(t, r"into my account|to my account|from another bank") and not howq_block:
        m_rej = re.search(r"reject|declin", t)
        m_fail = re.search(r"fail", t)
        if m_rej and (not m_fail or m_rej.start() < m_fail.start()):
            return "declined_transfer"
        return "failed_transfer"
    if has_tr and _has(t, r"(into|to|in) (my|this|the) (\w+ )?account|another bank|different bank|another account|other bank|foreign account|bank account|from my bank"):
        if _has(t, r"\b(how|can i|want|need|process|where|what|why can)\b") and not _has(t, r"another person|friend|someone|recipient|send"):
            return "transfer_into_account"
    return None


def _payment_fail(t):
    if not _has(t, r"\b(mortgage|rent|loan|bill)s?\b"):
        return None
    if _has(t, r"\bcard\b|\batm\b|withdraw|cash|deposit|cheque|check|refund|\bfee|exchange|statement|limit|pending|declin"):
        return None
    if _has(t, r"error|won'?t go through|not go through|didn'?t go through|fail|not working|isn'?t working|unable|couldn'?t|needs to go through"):
        return "failed_transfer"
    return None


def classify(text):
    try:
        t = " ".join(str(text).lower().replace("\u2019", "'").split())
        if not t:
            return None
        for f in (_deposit, _exchange, _wrong_amount, _card_declined, _transfer, _payment_fail):
            r = f(t)
            if r:
                return r
    except Exception:
        return None
    return None
