"""JSON-lines worker; third-party output is never written to protocol stdout."""
import contextlib
import json
import sys
import traceback


def main():
    output = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        from .analysis import AnalysisSession
        session = AnalysisSession()
        for line in sys.stdin:
            try:
                request = json.loads(line)
                result = {'ok': True, 'result': session.call(request['op'], request.get('args', {}))}
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                result = {'ok': False, 'error': str(exc)}
            output.write(json.dumps(result, ensure_ascii=True)+'\n')
            output.flush()


if __name__ == '__main__':
    main()
