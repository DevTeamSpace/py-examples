class CustomPaginator(Paginator):
    """
    A custom class inherited from the base Paginator class.
    Overrides the functions for direct search in the database.
    SearchPage as default.
    Need to add the following functions:

    CREATE OR REPLACE FUNCTION idx(anyarray, anyelement)
    RETURNS INT AS
    $$
    SELECT i FROM (
    SELECT generate_series(array_lower($1,1),array_upper($1,1))
    ) g(i)
    WHERE $1[i] = $2
    LIMIT 1;
    $$ LANGUAGE SQL IMMUTABLE;

    CREATE OR REPLACE FUNCTION get_page(value_id integer[], value_limit integer, value_offset integer)
    RETURNS SETOF products_product
    LANGUAGE plpgsql
    AS $function$
    BEGIN
    RETURN QUERY
    SELECT *
    FROM products_product
    WHERE id = ANY(value_id) AND is_active = TRUE
    ORDER BY idx(value_id, id) LIMIT value_limit OFFSET value_offset;
    END
    $function$

    CREATE OR REPLACE FUNCTION get_page_count(value_id integer[])
    RETURNS integer
    LANGUAGE plpgsql
    AS $function$
    BEGIN
    RETURN (
    SELECT COUNT(*)
    FROM products_product
    WHERE id = ANY(value_id) AND is_active = TRUE);
    END
    $function$;
    """

    def __init__(self, object_list, per_page, orphans=0,
                 allow_empty_first_page=True, slug=None, 
                 type_product=None, paginate_by=10, search_model=u'SearchPage'):
        self.object_list = object_list
        self.per_page = int(per_page)
        self.orphans = int(orphans)
        self.allow_empty_first_page = allow_empty_first_page
        self._num_pages = self._count = None
        self.slug = slug
        self.type_product = type_product
        self.paginate_by = paginate_by

        if search_model == u'SearchPage':
            page = SearchPage.objects.get(slug=self.slug, type=self.type_product)
            
        elif search_model == u'Special':
            page = Special.objects.get(slug=slug, type=u'listing')

        try:
            self.order_ids = [int(i) for i in page._ordered_m2m_ordering.split('[')[1]
                                                                        .split(']')[0]
                                                                        .split(',')]
        except:
            self.order_ids = []

    def page(self, number):
        number = self.validate_number(number)
        bottom = (number - 1) * self.per_page
        page_objects = list(Product.objects.raw('SELECT * FROM get_page(%s, %s, %s)', 
                            [self.order_ids, self.paginate_by, bottom]))
        
        return self._get_page(page_objects, number, self)

    def _get_count(self):
        if self._count is None:
            try:
                cursor = connection.cursor()
                cursor.execute('SELECT * FROM get_page_count(%s)', [self.order_ids])
                self._count = cursor.fetchone()[0]
            except (AttributeError, TypeError):
                self._count = len(self.object_list)
                
        return self._count
    
    count = property(_get_count)
