# QueryMind .NET API (design §14.1, E12). Build context: api/.
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

# Restore first so code changes don't re-download packages.
COPY Directory.Build.props QueryMind.sln ./
COPY QueryMind.Api/QueryMind.Api.csproj QueryMind.Api/
RUN dotnet restore QueryMind.Api/QueryMind.Api.csproj

COPY QueryMind.Api/ QueryMind.Api/
RUN dotnet publish QueryMind.Api/QueryMind.Api.csproj -c Release -o /out --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /out ./
# The aspnet image listens on 8080 and runs as the non-root "app" user by default.
USER app
EXPOSE 8080
ENTRYPOINT ["dotnet", "QueryMind.Api.dll"]
