from fastapi.templating import Jinja2Templates

from .services import ServiceError

templates=Jinja2Templates(directory="products/templates")
def ctx(request, **values): return {"request":request, "field_types":["text","number","boolean","date","select","text[]"], **values}
def message(exc): return exc.message if isinstance(exc,ServiceError) else str(exc)
