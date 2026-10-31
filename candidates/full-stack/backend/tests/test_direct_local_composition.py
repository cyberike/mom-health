"""Synthetic checks for the explicit, unexposed budgeted composition."""
import unittest
from pathlib import Path
from unittest.mock import patch

from railway_candidate.budgeted_sqlite import BudgetedSQLiteRepository
from railway_candidate.direct_candidate import create_direct_candidate
from railway_candidate.direct_local_composition import build_budgeted_local_config


class LocalCompositionTests(unittest.TestCase):
    def test_validation_precedes_repository_and_uses_exact_validated_path(self):
        events = []
        token = 'fictional-phone-token-12345678901234567890'
        password = 'fictional-caregiver-password-1234567890'
        repo = object()
        app = object()
        config = object()

        def validate(mount, database, *, private_child):
            events.append(('validate', mount, database, private_child))
            return 'validated-synthetic-path'

        def make_repo(path, *, require_existing):
            events.append(('repository', path, require_existing))
            return repo

        def make_app(host, user, passwd, phone, database, mount, **kwargs):
            events.append(('app', database, kwargs['repository']))
            self.assertEqual('validated-synthetic-path', database)
            self.assertIs(repo, kwargs['repository'])
            self.assertTrue(kwargs['private_child'])
            self.assertTrue(kwargs['require_existing_storage'])
            return app

        def make_config(actual_app, *, port):
            events.append(('config', actual_app, port))
            return config

        with patch('railway_candidate.direct_local_composition.validate_existing_storage', side_effect=validate), \
             patch('railway_candidate.direct_local_composition.BudgetedSQLiteRepository', side_effect=make_repo), \
             patch('railway_candidate.direct_local_composition.create_direct_candidate', side_effect=make_app), \
             patch('railway_candidate.direct_local_composition.build_local_config', side_effect=make_config), \
             patch('sqlite3.connect') as sqlite_connect, patch('socket.socket') as socket_constructor:
            result = build_budgeted_local_config('synthetic.example.com', 'caregiver', password, token,
                '/synthetic/db', '/synthetic', private_child=True, port=1234)
        self.assertIs(config, result)
        self.assertEqual(['validate', 'repository', 'app', 'config'], [event[0] for event in events])
        self.assertEqual(('repository', 'validated-synthetic-path', True), events[1])
        sqlite_connect.assert_not_called()
        socket_constructor.assert_not_called()

    def test_rejects_before_repository_when_fixture_or_validation_invalid(self):
        args = ('synthetic.example.com', 'caregiver', 'x' * 40, 'y' * 40, '/fixture/db', '/fixture')
        with patch('railway_candidate.direct_local_composition.validate_existing_storage', side_effect=ValueError('unsafe')), \
             patch('railway_candidate.direct_local_composition.BudgetedSQLiteRepository') as repository:
            with self.assertRaises(ValueError):
                build_budgeted_local_config(*args)
        repository.assert_not_called()

    def test_invalid_local_parameters_fail_before_storage(self):
        args = ('synthetic.example.com', 'caregiver', 'x' * 40, 'y' * 40, '/fixture/db', '/fixture')
        with patch('railway_candidate.direct_local_composition.validate_existing_storage') as validate:
            with self.assertRaises(ValueError): build_budgeted_local_config(*args, port=True)
            with self.assertRaises(ValueError): build_budgeted_local_config(*args, private_child='true')
            with self.assertRaises(TypeError): build_budgeted_local_config(*args, repository=object())
        validate.assert_not_called()

    def test_existing_only_factory_rejects_missing_unbudgeted_or_mismatched_repository(self):
        expected = str(Path(__file__).resolve().parent / 'fictional.sqlite3')
        other = str(Path(__file__).resolve().parent / 'fictional-other.sqlite3')
        arguments = ('synthetic.example.com', 'caregiver', 'x' * 40, 'y' * 40, expected, str(Path(expected).parent))
        bad_repositories = (None, object(), BudgetedSQLiteRepository(expected),
                            BudgetedSQLiteRepository(other, require_existing=True))
        with patch('railway_candidate.direct_candidate.validate_existing_storage', return_value=expected), \
             patch('railway_candidate.direct_candidate.create_app') as create_app:
            for repo in bad_repositories:
                with self.subTest(repository=type(repo).__name__), self.assertRaises(ValueError):
                    create_direct_candidate(*arguments, repository=repo, require_existing_storage=True)
        create_app.assert_not_called()

    def test_existing_only_factory_accepts_matching_budgeted_repository(self):
        expected = str(Path(__file__).resolve().parent / 'fictional.sqlite3')
        arguments = ('synthetic.example.com', 'caregiver', 'x' * 40, 'y' * 40, expected, str(Path(expected).parent))
        repo = BudgetedSQLiteRepository(expected, require_existing=True)
        with patch('railway_candidate.direct_candidate.validate_existing_storage', return_value=expected), \
             patch('railway_candidate.direct_candidate.create_app') as create_app:
            gateway = create_direct_candidate(*arguments, repository=repo, require_existing_storage=True)
        self.assertTrue(callable(gateway))
        self.assertIs(repo, create_app.call_args.kwargs['repository'])


if __name__ == '__main__': unittest.main()
