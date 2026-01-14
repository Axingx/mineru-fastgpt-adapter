```
# docker build -t mineru-adapter:v1.0 .
# docker save -o mineru-adapter-v1.0.tar mineru-adapter:v1.0

# mkdir mineru_temp_images
# chmod 777 mineru_temp_images

location /mineru_images/ {
    proxy_pass http://127.0.0.1:3333/mineru_images/;
}

```
