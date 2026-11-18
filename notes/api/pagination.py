from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 9  # same page size as the web feed
    page_size_query_param = "page_size"
    max_page_size = 50
