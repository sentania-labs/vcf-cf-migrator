"""Many account values must not make ordinary content logging unusable."""
import time

from vcfcf_migrator.runlog import Redactor


def test_many_people_do_not_slow_unrelated_strings_quadratically():
    redactor = Redactor()
    for i in range(339):
        redactor.person(f'Invented Person {i:04d}')
    text = 'AlertDefinition-Synthetic alert for virtual machine capacity and storage'
    redactor.text(text)  # compile outside the measurement
    started = time.perf_counter()
    for _ in range(1000):
        assert redactor.text(text) == text
    # Old named alternatives take over two seconds here. This generous bound
    # catches that defect without depending on a precise workstation speed.
    assert time.perf_counter() - started < 1.0


def test_fast_scan_preserves_named_matcher_semantics():
    redactor = Redactor()
    redactor.owner('Smith')
    for value in ('Smith Jones', 'İpek', 'Kelvin', 'Longs', 'Brock',
                  'aaaa1111-0000-4000-8000-00000000000a'):
        redactor.person(value)
    values = ('SMITH JONES', 'Smith', 'ıpek', 'İPEK', 'i\u0307pek', 'KELVIN',
              'Longſ', 'Brock-login', 'notBrock', '-Brock', ' Brock',
              'Condition_aaaa1111-0000-4000-8000-00000000000a',
              'Smith Jones and Brock and İpek', 'ordinary text')
    pattern = redactor._compiled()
    for text in values:
        expected = pattern.sub(redactor._replacement_for, text)
        assert redactor.text(text) == expected
    redactor.person('Newly Learned')
    assert redactor.text('Newly Learned') == '[excluded:person]'
