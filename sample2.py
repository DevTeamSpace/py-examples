class ShowcaseView(TemplateView):
    template_name = 'showcase/showcase.html'

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        slug = self.kwargs.get('slug')
        showcase_page = ShowcasePage.objects.filter(slug=slug, is_active=True).first()

        if not showcase_page or self.kwargs.get('option_slug', None):
            raise Http404

        context['showcase_page'] = showcase_page
        context['products'] = [self.set_showable_price(p) for p in showcase_page.products.all()]
        context['similar_products'] = [self.set_showable_price(p) for p in showcase_page.similar_tours.all()]

        agents_group = TeamGroup.objects.filter(is_agent=True).first()
        context['agents'] = agents_group.members.order_by('?')[:3]
        return context

    def set_showable_price(self, product):
        product.showable_price = product.lowest_price
        lowest = product.options.filter(prices__base_rate=product.showable_price).first()
        product.is_lux = lowest.is_pack_or_lux if lowest else None
        return product
