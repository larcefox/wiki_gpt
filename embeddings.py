# пример с ML SDK
from yandex_cloud_ml_sdk import YCloudML
import os

sdk = YCloudML(folder_id=os.getenv("YANDEX_FOLDER_ID"), auth=os.getenv("YANDEX_OAUTH_TOKEN"))
# List available models
print(dir(sdk.models))  # This will show you the available methods and attributes of the sdk.models object
models = sdk.models.list()
print(models)
