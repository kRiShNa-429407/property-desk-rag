import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from catalog import extra_fields, query_catalog
from llm import ask
from rag import Passage
from mongo_fixtures import MongoCase


class CatalogTests(MongoCase):
    def setUp(self):
        super().setUp()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        rows = []
        examples = [(1, 'Riyadh', 'Apartment', 650000, 3, 'sale'),
                    (2, 'Riyadh', 'Apartment', 950000, 3, 'sale'),
                    (3, 'Riyadh', 'Apartment', 1200000, 4, 'sale'),
                    (4, 'Jeddah', 'Villa', 800000, 3, 'sale'),
                    (5, 'Riyadh', 'Villa', 1500000, 5, 'sale'),
                    (6, 'Riyadh', 'Apartment', None, 3, 'sale'),
                    (7, 'Riyadh', 'Apartment', 500000, 2, 'sale'),
                    (8, 'Riyadh', 'Apartment', 30000, 3, 'rent')]
        for identifier, city, kind, price, beds, purpose in examples:
            rows.append({'id': identifier, 'updatedAt': '2026-11-18',
                         'propertyInfo': {'slug': f'home-{identifier}', 'title': kind, 'city': city,
                                          'district': 'Riyadh' if identifier == 7 else 'Centre',
                                          'propertySubType': kind, 'propertyFor': purpose,
                                          'salePrice': price if purpose == 'sale' else None,
                                          'expectedRent': price if purpose == 'rent' else None,
                                          'currencyType': 'SAR'},
                         'attributes': [{'name': 'Bedrooms', 'value': beds}]})
        self.rows = rows
        self.load_rows(self.directory.name, rows)

    def test_city_name_is_not_accidentally_treated_as_a_district(self):
        result = query_catalog(self.collection, 'How many properties in Riyadh?')
        self.assertIn('7 property listings in Riyadh', result[0]['answer'])

    def test_bedrooms_and_budget_are_exact_and_missing_prices_do_not_match(self):
        result = query_catalog(self.collection, 'Show apartments for sale in Riyadh with 3 bedrooms under SAR 1 million')
        self.assertEqual({row['url'].split('/')[-1] for row in result[1:]}, {'home-1', 'home-2'})
        count = query_catalog(self.collection, 'How many 3-bedroom apartments for sale in Riyadh under 1m?')
        self.assertIn('**2 apartment listings', count[0]['answer'])
        between = query_catalog(self.collection, 'Show apartments for sale in Riyadh between 700k and 1m')
        self.assertEqual(len(between), 2)
        self.assertTrue(between[1]['url'].endswith('home-2'))

    def test_average_uses_all_priced_matches_and_excludes_nulls(self):
        result = query_catalog(self.collection, 'What is the average price of apartments for sale in Riyadh?')
        self.assertIn('SAR 825,000.00', result[0]['answer'])
        self.assertIn('4 apartment listings', result[0]['answer'])

    def test_bedroom_ranges_and_reversed_wording_are_filtered(self):
        result = query_catalog(self.collection, 'How many apartments for sale in Riyadh with bedrooms at least 3?')
        self.assertIn('**4 apartment listings', result[0]['answer'])
        result = query_catalog(self.collection, 'Show apartments for sale in Riyadh with at most 2 bedrooms')
        self.assertEqual(len(result), 2)
        self.assertTrue(result[1]['url'].endswith('home-7'))

    def test_cheapest_is_sorted_numerically_over_all_matching_rows(self):
        result = query_catalog(self.collection, 'What is the cheapest apartment for sale in Riyadh?')
        self.assertEqual(len(result), 2)
        self.assertTrue(result[1]['url'].endswith('home-7'))

    def test_unsupported_filters_and_mixed_rental_prices_are_not_ignored(self):
        result = query_catalog(self.collection, 'How many villas with a pool in Riyadh?')
        self.assertIn("can't reliably filter", result[0]['answer'])
        multiple = query_catalog(self.collection, 'Show villas in Riyadh and Jeddah')
        self.assertIn('one location at a time', multiple[0]['answer'])
        average = query_catalog(self.collection, 'What is the average price of apartments in Riyadh?')
        self.assertIn('buy or rent', average[0]['answer'])
        unknown = query_catalog(self.collection, 'How many farm properties in Riyadh?')
        self.assertIn("can't reliably filter", unknown[0]['answer'])

    def test_exact_database_answers_do_not_call_or_depend_on_the_model(self):
        result = query_catalog(self.collection, 'How many villas in Riyadh?')[0]
        passage = Passage('Wasalt', result['title'], result['url'], 'unknown', result['text'], result['answer'])
        client = Mock()
        answer, model = ask('How many villas?', [passage], '', session=client)
        self.assertEqual(answer, result['answer'])
        self.assertEqual(model, 'wasalt-catalog')
        client.post.assert_not_called()

    def test_unknown_values_are_not_converted_to_zero(self):
        self.assertEqual(extra_fields('Bedrooms: Not specified\nPurpose: sale'), (None, '', None, ''))
        self.assertEqual(extra_fields('Purpose: sale\nListed sale price: SAR 650000.50\nBedrooms: 3')[:3],
                         (650000.5, 'SAR', 3))

    def test_reimport_preserves_all_original_listing_fields(self):
        before = {row['_id']: row['raw'] for row in self.collection.find()}
        self.load_rows(self.directory.name, self.rows)
        after = {row['_id']: row['raw'] for row in self.collection.find()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
