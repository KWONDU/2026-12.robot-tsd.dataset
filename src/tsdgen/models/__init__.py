"""정규 데이터 모델 (스펙 5절).

SceneState / ScenarioRecord / TaskSituationDescription / Sample / VerificationResult. JSON Schema는 schemas
스테이지가 이 모델들에서 자동 생성한다. 스키마 버전 값 자체는 config(schema_versions)가 단일 출처다.
성격별로 파일을 나눴고(base, scene, tsd, verification, sample), 쓰는 쪽은 `from tsdgen.models import X`로 가져온다.
"""

from tsdgen.models.base import *  # noqa: F403
from tsdgen.models.sample import *  # noqa: F403
from tsdgen.models.scene import *  # noqa: F403
from tsdgen.models.tsd import *  # noqa: F403
from tsdgen.models.verification import *  # noqa: F403
