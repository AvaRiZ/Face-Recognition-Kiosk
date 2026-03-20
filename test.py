
import torch, sys
import tensorflow as tf
print(tf.__version__)
print(tf.config.list_physical_devices("GPU"))

print("Python", sys.version)
print("Torch", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
print("CUDA version:", torch.version.cuda)
print("GPU count:", torch.cuda.device_count())

