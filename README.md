# S3 Bucket CLI

კომფორტული Python CLI Amazon S3 Bucket-ებთან სამუშაოდ. პროექტი იყენებს **Poetry**, **python-dotenv**, **logging**, **boto3**, **Click** და **filetype** ბიბლიოთეკებს.

## ინსტალაცია

საჭიროა Python 3.11+ და Poetry 2.x ([Poetry installation](https://python-poetry.org/docs/#installation)).

```bash
poetry install
```

შექმენით `.env` ფაილი `.env.example`-ის ასლით:

```bash
# Linux / macOS
cp .env.example .env
# Windows PowerShell
Copy-Item .env.example .env
```

შეავსეთ AWS მონაცემები `.env`-ში ან გამოიყენეთ AWS profile / IAM role. `.env` და ლოგები Git-ში არ იტვირთება. გარემოში უკვე განსაზღვრულ ცვლადებს dotenv არ გადაწერს. ბრძანებები გაუშვით პროექტის ძირეული საქაღალდიდან, ან მიუთითეთ `--env-file`.

```dotenv
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=us-east-1
```

დროებითი credentials-ისთვის დაამატეთ `AWS_SESSION_TOKEN`. Production-ში სასურველია IAM role ან AWS profile.

## გამოყენება

```bash
poetry run s3-cli --help
poetry run s3-cli list-buckets
poetry run s3-cli --region eu-west-1 create-bucket my-unique-media-bucket
poetry run s3-cli bucket-exists my-unique-media-bucket
poetry run s3-cli --verbose upload-url my-unique-media-bucket "https://example.com/photo.png"
poetry run s3-cli upload-url my-unique-media-bucket "https://example.com/photo" --key images/photo.png --max-size-mb 20
poetry run s3-cli set-object-access my-unique-media-bucket images/photo.png --acl private
poetry run s3-cli generate-public-read-policy my-unique-media-bucket > public-policy.json
poetry run s3-cli create-bucket-policy my-unique-media-bucket --file public-policy.json
poetry run s3-cli read-bucket-policy my-unique-media-bucket
poetry run s3-cli delete-bucket my-unique-media-bucket
```

URL-ები მაგალითებია; ატვირთვისთვის ჩაანაცვლეთ რეალური მედია ფაილის URL-ით.

გლობალური პარამეტრები (`--region`, `--env-file`, `--endpoint-url`, `--verbose`) იწერება ქვე-ბრძანებამდე. დესტრუქციულ / საჯარო წვდომის ბრძანებებზე არის დადასტურება; ავტომატიზაციისთვის გამოიყენეთ `--yes`.

`bucket-exists` ბეჭდავს `true`/`false`: არსებული bucket → exit 0; არარსებული → exit 1. წვდომის უარყოფაც exit 1-ია, მაგრამ ცალკე error ტექსტით. სხვა შეცდომები გამოდის stderr-ში; არასწორი CLI არგუმენტები → exit 2. პოლიტიკის არქონისას `read-bucket-policy` ბეჭდავს `null`.

## მოთხოვნილი ფუნქციები

ყველა ფუნქცია არის `src/s3_bucket_cli/s3.py`-ში და შესაძლებელია პირდაპირ import-იც.

| ფუნქცია | CLI ბრძანება |
| --- | --- |
| `init_client()` | კლიენტი იქმნება ავტომატურად, საჭიროებისას |
| `list_buckets()` | `list-buckets` |
| `create_bucket()` | `create-bucket BUCKET` |
| `delete_bucket()` | `delete-bucket BUCKET` |
| `bucket_exists()` | `bucket-exists BUCKET` |
| `download_file_and_upload_to_s3()` | `upload-url BUCKET URL` |
| `set_object_access_policy()` | `set-object-access BUCKET KEY --acl private` |
| `generate_public_read_policy()` | `generate-public-read-policy BUCKET` |
| `create_bucket_policy()` | `create-bucket-policy BUCKET --file policy.json` |
| `read_bucket_policy()` | `read-bucket-policy BUCKET` |

## ფაილების ვალიდაცია

ფაილის ტიპი განისაზღვრება **შიგთავსის ბინარული ხელმოწერით**, `filetype` ბიბლიოთეკით. URL-ის გაფართოებასა და HTTP `Content-Type` სათაურს არ ვენდობით. დაშვებულია მხოლოდ:

| გაფართოება | რეალური MIME ტიპი |
| --- | --- |
| `.bmp` | `image/bmp` |
| `.jpg`, `.jpeg` | `image/jpeg` |
| `.png` | `image/png` |
| `.webp` | `image/webp` |
| `.mp4` | `video/mp4` |

- HTML/PDF/GIF ან უცნობი ტიპი უარყოფილია, თუნდაც URL მთავრდებოდეს `.jpg`-ით.
- სახელი ავტომატურად იღებს აღმოჩენილი ტიპის გაფართოებას: PNG მონაცემები `photo.jpg` URL-ზე აიტვირთება როგორც `photo.png`.
- `--key`-ით მითითებული გაფართოება უნდა ემთხვეოდეს რეალურ ტიპს; JPEG-ისთვის `.jpg` და `.jpeg` ორივე დაშვებულია, რეგისტრის მიუხედავად.
- ცარიელი ფაილი და ზედმეტი ზომა უარყოფილია. ჩამოტვირთვა სტრიმინგულია, ნაგულისხმევი ლიმიტი 100 MiB, კავშირის timeout 10 წამი, წაკითხვის 30 წამი, ხოლო ჩამოტვირთვის ბიუჯეტი 300 წამი (მოწმდება ყოველ მიღებულ chunk-ზე).
- ფაილი დროებით დისკზე ინახება; ვალიდაციამდე S3-ზე არაფერი იტვირთება. დროებითი ფაილი შეცდომის დროსაც იხურება და იშლება.
- S3 ობიექტს ენიჭება აღმოჩენილი MIME `ContentType`.

MIME signature detection არ არის ფაილის სრული დეკოდირება, მთლიანობის ან ვირუსების შემოწმება; დაზიანებულმა ფაილმა სწორი ხელმოწერით შეიძლება შემოწმება გაიაროს. URL downloader გამოიყენეთ სანდო წყაროებზე, როგორც ლოკალური CLI; ის არ წარმოადგენს საჯარო სერვერისთვის SSRF ფილტრს.

## S3 წვდომა და შეზღუდვები

- გამოიყენება ჩვეულებრივი S3 general purpose bucket-ები. სახელები S3-ში გლობალურად უნიკალური უნდა იყოს; საბოლოო ვალიდაციას აკეთებს AWS.
- `us-east-1`-ში შექმნისას `LocationConstraint` არ იგზავნება; სხვა რეგიონებში იგზავნება.
- `delete-bucket` შლის მხოლოდ ცარიელ bucket-ს. ობიექტებსა და version-ებს ავტომატურად არ შლის.
- `set_object_access_policy()` იყენებს object ACL-ს. ახალ AWS bucket-ებში `BucketOwnerEnforced` ნაგულისხმევად თიშავს ACL-ებს; ასეთ შემთხვევაში გამოიყენეთ bucket policy. პროგრამა ავტომატურად არ ცვლის ownership-ს ან Block Public Access-ს.
- საჯარო policy ყველას აძლევს `s3:GetObject` წვდომას შესაბამის ობიექტებზე. მის შექმნამდე დაფიქრდით, უნდა იყოს თუ არა ფაილები საჯარო. `generate-public-read-policy` მხოლოდ JSON-ს ქმნის; `create-bucket-policy` რეალურად ცვლის / ანაცვლებს არსებულ პოლიტიკას. საჯარო policy-ს AWS Block Public Access-მა შეიძლება უარი უთხრას.
- საჭიროა შესაბამისი IAM უფლებები: `s3:ListAllMyBuckets`, `s3:CreateBucket`, `s3:DeleteBucket`, `s3:ListBucket` (head bucket), `s3:PutObject`, `s3:PutObjectAcl`, `s3:PutBucketPolicy`, `s3:GetBucketPolicy`; multipart ატვირთვის გასაწმენდად `s3:AbortMultipartUpload`. შეზღუდეთ უფლებები საჭირო bucket-ებით, სადაც შესაძლებელია.
- `--verbose` აჩვენებს მხოლოდ აპლიკაციის ოპერაციების INFO ლოგებს stderr-ში; AWS credentials და საწყისი URL არ ილოგება.

## ტესტირება

```bash
poetry run pytest -q
poetry run ruff check .
poetry build
```

ტესტებს AWS ანგარიში, ქსელი ან რეალური bucket არ სჭირდება: S3 მოწმდება botocore Stubber-ით, HTTP — mock-ებით, სურათები კი იქმნება Pillow-ით (მხოლოდ dev dependency). მოწმდება ყველა დაშვებული ტიპი, სახელების/MIME შესაბამისობა, ყალბი JPG, ცარიელი ფაილი, ზომის ლიმიტი, HTTP შეცდომები, რეგიონები, 403/404, policy, ACL და CLI დადასტურება. რეალურ AWS-ზე ინტეგრაცია ცალკე მოითხოვს თქვენს credentials-ს.

## ოფიციალური რესურსები

- [Boto3 S3 reference](https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html)
- [S3 object ACL და BucketOwnerEnforced](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object_acl.html)
- [filetype — binary signature detection](https://pypi.org/project/filetype/)
