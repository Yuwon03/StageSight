# StageSight 촬영 장소 공급처 확장 조사

- 작성일: 2026-09-02
- 대상: StageSight 개발 및 해커톤 제출
- 범위: 대한민국 촬영 장소·렌탈 스튜디오 데이터 공급처, 공개 접근 정책, 현재 코드 확장성
- 전제: 페이지가 기술적으로 공개되어 있다는 사실만으로 복제·재게시 권한이 생기지는 않는다. `robots.txt`는 크롤링 신호이며 콘텐츠 라이선스 계약을 대신하지 않는다.

## 결론

StageSight는 여러 공급처를 수용할 수 있다. 현재 SQLite 저장소, 검색 API, 상세 화면은 `KoreanLocation` 형태만 맞으면 공급처에 거의 독립적이다. 반면 크롤러 실행부는 Hourplace의 sitemap 함수, `hp_` ID, 단일 전역 crawl 상태에 직접 결합돼 있어 provider adapter 구조로 바꿔야 한다.

가장 안전하고 효과적인 확장 순서는 다음과 같다.

1. 이용허락 범위가 명확한 공공데이터를 `reference location` 유형으로 추가한다.
2. Unhide와 FilmKorea에 StageSight의 출처 링크·예약 유입 방식으로 데이터 사용 허가 또는 feed 제공을 요청한다.
3. SpaceCloud, Filmplace, PlaceHub는 공개 페이지 크롤링을 바로 구현하지 말고 공식 제휴/API/서면 허가를 먼저 받는다.
4. Hourplace도 공개 Cloud 서비스로 운영하기 전 콘텐츠·사진 재게시와 AI 입력 사용 범위를 서면 확인한다.

## 현재 코드의 확장성

### 그대로 재사용 가능한 부분

- `KoreanLocation` Pydantic 모델로 정규화된 레코드 저장
- 공급처별 prefix를 쓸 수 있는 문자열 ID
- SQLite upsert, revision, delta sync, 검색, delisting
- 실제 원문 URL과 검증 상태를 담는 citation 구조
- 프런트엔드의 출처 badge와 원본 예약 링크
- 외부 이미지 URL을 처리하는 image proxy
- 공급처와 무관하게 동작하는 대본-장소 매칭

### 공급처 추가 전에 바꿔야 하는 부분

- `services/crawler/worker.py`가 Hourplace 함수와 `hp_`에 직접 의존한다.
- `crawl_status`와 `last_crawl`이 공급처별이 아니라 전역 1개다.
- 스키마에 `provider`, `provider_listing_id`, `listing_kind`, 위도·경도, 원본 갱신일, 콘텐츠 사용권 상태가 없다.
- 동일 장소가 여러 플랫폼에 등록됐을 때 합칠 canonical location ID와 중복 판정 테이블이 없다.
- `KoreanLocation` 필수 필드가 많아 공공 로케이션처럼 가격·예약이 없는 데이터에 억지 기본값이 필요하다.
- UI 문구와 빈 카탈로그 안내가 Hourplace 전용으로 남아 있다.
- 삭제 판정은 공급처별 전체 목록 또는 해당 pass에서 실제 확인한 ID 집합을 분리해야 한다.

권장 provider 계약은 다음 네 단계다.

```python
class ListingProvider(Protocol):
    name: str
    id_prefix: str

    async def discover_ids(self) -> list[str]: ...
    async def fetch_listing(self, source_id: str) -> RawListing | None: ...
    def normalize(self, raw: RawListing) -> KoreanLocation: ...
    def rights(self) -> ContentRights: ...
```

DB에는 최소한 `source_provider`, `source_listing_id`, `canonical_location_id`, `listing_kind`, `source_updated_at`, `last_verified_at`, `rights_status`를 별도 열로 두는 것이 좋다. `listing_kind`는 `bookable`, `inquiry_only`, `reference`로 나눠야 사용자가 “바로 대관 가능한 매물”과 “촬영 후보 장소”를 혼동하지 않는다.

## 후보 공급처 평가

| 후보 | 데이터 성격 | 기술적 수집성 | 권리·정책 상태 | StageSight 적합성 | 권고 |
|---|---|---:|---|---:|---|
| Unhide | 촬영·팝업용 큐레이션, 가격·면적·주차·시설 | 높음: 서버 렌더링 상세 페이지 | robots는 일반 접근을 허용하지만 재게시 라이선스는 확인되지 않음 | 매우 높음 | 가장 먼저 제휴 문의 |
| FilmKorea | 전국 영상위원회 로케이션, 공공·산업시설, 스튜디오·세트 | 높음: JSP 목록·상세 페이지 | 서비스·회원 게시물 권리와 영리 사용 제한 때문에 대량 재게시 전 허가 필요 | 매우 높음 | 공식 feed/사용 허가 요청 |
| SpaceCloud | 대규모 일반 공간대여 및 촬영 스튜디오 | 높음: 공개 상세·host 페이지, sitemap | 콘텐츠 표시 페이지가 동의 없는 복제·도용을 금지 | 높음 | 제휴/API 허가 전 크롤링 금지 |
| Filmplace | 영화·광고 촬영 전문 글로벌 매물 | 중간: 상세 페이지는 검색 가능하나 일부 화면은 동적 | 약관이 bot, crawler, scraper를 통한 수집을 명시적으로 금지 | 높음 | 파트너 계약만 허용 |
| PlaceHub | 일반 공간대여, 촬영 태그·가격·평수 | 높음 | 회사 콘텐츠의 복제·전송·배포·2차 이용은 사전 동의 필요 | 중간 | 서면 허가 후 사용 |
| 공공데이터포털 미디어 촬영지 | 작품 촬영지 15,034행, 주소·설명·영업시간 | 매우 높음: CSV 및 신청형 API | 무료, 이용허락범위 제한 없음 | 중간 | 즉시 가능한 안전한 참고 DB |
| 공공데이터포털 경기 촬영지원 | 실제 촬영지원 작품·촬영 장소 이력 | 매우 높음: JSON/XML API | 이용허락범위 제한 없음 | 보조적 | 촬영 이력·신뢰 신호로 추가 |

### 1. Unhide

촬영 전용 분류가 있고 가격, 면적, 주차, 편의시설이 상세 페이지에 비교적 구조적으로 나타난다. 예를 들어 한 상세 페이지에는 50평, 시간당 가격, 주차 1대, 냉난방기·탈의실 등이 함께 제공된다. StageSight가 원하는 공간 판단 정보와 가장 가깝다.

다만 공개 페이지와 robots 허용만 확인됐고 제3자 서비스에서 사진·설명을 복제해 보여줄 수 있다는 명시적 라이선스는 찾지 못했다. `unhideofficial@gmail.com`에 metadata feed, thumbnail 사용, 원문 예약 링크, AI 이미지 입력 허용 범위를 문의하는 것이 우선이다.

출처: [Unhide 스튜디오 목록](https://unhide.space/setstudio), [Unhide 상세 예시](https://unhide.space/setstudio/?idx=46)

### 2. FilmKorea와 지역 영상위원회 DB

FilmKorea는 강원, 경남, 대전, 서울, 인천, 전남, 전북, 제주, 충북, 충남 등 11개 지역 데이터를 제공하며 경기·부산·전주 등 별도 지역 DB로 연결한다. 상업 대관 매물뿐 아니라 도로, 경찰서, 폐시설, 공원, 철도시설과 대형 스튜디오·세트 정보를 얻을 수 있어 Hourplace의 편향을 크게 보완한다.

다만 모든 장소가 즉시 예약 가능한 것은 아니다. 문의처와 촬영지원 절차로 이어지는 `reference` 또는 `inquiry_only` 데이터로 표시해야 한다. 이용약관은 위원회 동의 없는 영리 목적 사용을 제한하고 게시물 권리가 작성자에게 있다고 밝히므로, 사진과 설명을 대량 복제하기 전 `kfcin@kfcin.or.kr`에 허가나 feed를 요청해야 한다.

출처: [FilmKorea 로케이션 검색](https://www.filmkorea.or.kr/location/location.jsp?s_category01_code=04&s_category02_code=04), [스튜디오 상세 예시](https://www.filmkorea.or.kr/studio/studio_view.jsp?id=10200), [FilmKorea 이용약관](https://www.filmkorea.or.kr/member/clause.jsp)

### 3. SpaceCloud

촬영 스튜디오, 자연광, 호리존, 유튜브 촬영, 가격, 인원, 후기 등 풍부한 실매물 데이터를 제공한다. 현재 프런트엔드도 이미 `스페이스클라우드` 출처 badge를 인식한다.

그러나 일반 crawler의 `/search` 접근은 robots에서 제한되고, 콘텐츠 표시 페이지는 동의 없는 무단 복제·도용을 금지한다고 명시한다. 따라서 sitemap이나 공개 상세 페이지가 보이더라도 StageSight DB로 복제해서는 안 된다. 제휴 문의에서 공식 API, affiliate deep link, 최소 metadata/thumbnail feed를 요청해야 한다.

출처: [SpaceCloud 콘텐츠](https://www.spacecloud.kr/contents), [robots.txt](https://www.spacecloud.kr/robots.txt), [콘텐츠산업진흥법 표시](https://www.spacecloud.kr/contentsinfo)

### 4. Filmplace

촬영 장소에 특화되어 있고 주거·사무·상업·야외·대형 시설, 촬영 인원, 주차, 장비, 비상탈출구 등의 필터를 제공한다. 해외 확장에도 유리하다.

하지만 이용약관은 bot, crawler, scraper에 의한 데이터 수집과 플랫폼 콘텐츠의 복사·표시를 명시적으로 금지한다. 직접 크롤러를 작성하면 안 되며 API나 데이터 제휴 계약을 받아야 한다.

출처: [Filmplace 촬영 장소](https://www.filmplace.co/ko/search?category_type=2), [Filmplace 이용약관](https://www.filmplace.co/en/help/article/29/terms-of-service)

### 5. PlaceHub

촬영 목적, 평수, 가격, 최대 인원 등 정규화하기 쉬운 데이터를 제공한다. 다만 약관은 회사가 제작·제공하는 콘텐츠를 사전 동의 없이 복제·전송·배포하거나 2차 이용할 수 없다고 규정한다. 규모와 촬영 전문성은 SpaceCloud·Filmplace보다 낮아 제휴 우선순위는 뒤다.

출처: [PlaceHub](https://placehub.co.kr/), [PlaceHub 이용약관](https://placehub.co.kr/terms)

### 6. 공공데이터포털

한국문화정보원의 미디어 촬영지 데이터는 15,034행 CSV와 API로 제공되며 장소명, 설명, 주소, 영업시간, 휴무일 등을 포함한다. 무료이고 이용허락범위 제한 없음으로 표시되어 있어 가장 안전하게 추가할 수 있다. 다만 2022년 기준 일회성 데이터라 현재 운영·촬영 허용·대관 가능성을 보장하지 않는다. 사진·시간당 가격도 없다.

따라서 StageSight에서는 `과거 작품 촬영지/참고 후보`로만 보여주고, `현재 대관 가능 여부 미확인` 경고와 최신 원문 또는 공식 연락처 확인 단계를 제공해야 한다.

출처: [한국문화정보원 미디어 촬영지 데이터](https://www.data.go.kr/data/15111405/fileData.do?recommendDataYn=Y), [경기도 촬영지원 현황 API](https://www.data.go.kr/data/15057839/openapi.do)

## 중복과 데이터 무결성

동일 스튜디오가 Hourplace와 SpaceCloud에 동시에 등록되는 실제 사례가 있다. 단순히 공급처별 ID만 저장하면 검색 결과가 중복되고, 가격·이름·사진이 서로 다른 레코드로 보인다.

권장 중복 판정 순서는 다음과 같다.

1. 정규화된 전화번호·사업자/운영사 ID가 있으면 확정 매칭
2. 정확한 위도·경도 30m 이내와 정규화된 이름 유사도
3. 주소 지번/도로명 정규화와 이미지 perceptual hash
4. 자동 점수가 애매하면 별도 후보로 유지하고 관리자 검토

사용자에게는 하나의 canonical 장소 카드 안에 `Hourplace에서 예약`, `SpaceCloud에서 예약`처럼 여러 source offer를 보여주는 방식이 적합하다. 가격은 공급처별로 보존해야 하며 임의로 하나를 대표 가격으로 덮어쓰면 안 된다.

## 구현 순서

### Phase 1: 멀티소스 기반 구조

- `ListingProvider` interface와 provider registry 추가
- Hourplace 코드를 첫 adapter로 이동
- 공급처별 crawl run/status/error/last successful sync 저장
- schema에 provider, listing kind, lat/lng, rights status 추가
- UI 문구를 Hourplace 전용에서 `실제 출처 제공 장소`로 변경
- 공급처 필터와 원문 링크 제공
- 공급처별 delisting 테스트 추가

### Phase 2: 권리 명확한 데이터

- 한국문화정보원 CSV importer 추가
- `reference` badge와 `현재 대관 여부 미확인` 표시
- 실매물만 원하는 검색에서는 기본 제외하거나 별도 토글 제공
- 경기 촬영지원 데이터를 장소의 `실제 촬영 이력` 신호로 연결

### Phase 3: 제휴 공급처

- Unhide에 우선 문의
- FilmKorea에 공공·영상위원회 metadata feed와 이미지 사용 허가 문의
- SpaceCloud·Filmplace·PlaceHub에는 예약 유입을 전제로 API/affiliate feed 요청
- 허가 문서에 허용 필드, 사진 hotlink/cache, Gemini 입력, 저장 기간, 갱신 주기, 삭제 처리 의무를 기록

## 최종 권고

바로 다음 구현은 상업 사이트의 새 scraper가 아니라 `멀티소스 adapter + public-data importer`여야 한다. 이렇게 하면 데이터를 늘리면서도 제출물에 “무단 수집” 위험을 만들지 않고, 제휴가 승인되는 순간 각 provider만 추가하면 된다.

상업 플랫폼을 대상으로는 최소 metadata만 저장하고 원문 예약으로 트래픽을 돌려주는 모델을 제안하는 것이 협상에 유리하다. 사진을 로컬에 영구 복제하거나 Gemini 학습 데이터라고 표현하지 말고, 허가된 경우에만 사용자가 선택한 원본 사진을 일회성 시뮬레이션 입력으로 처리하는 범위를 별도로 합의해야 한다.
