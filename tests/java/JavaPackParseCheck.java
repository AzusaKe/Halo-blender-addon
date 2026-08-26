import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import network.azusake.halo.data.HaloDefinition;
import network.azusake.halo.json.HaloDefinitionDeserializer;

/** Read-only compatibility check using Halo's compiled Java parser. */
public final class JavaPackParseCheck {
    public static void main(String[] args) throws Exception {
        Gson gson = new GsonBuilder()
            .registerTypeAdapter(HaloDefinition.class, new HaloDefinitionDeserializer())
            .create();
        int definitions = 0;
        try (ZipFile zip = new ZipFile(Path.of(args[0]).toFile())) {
            var entries = zip.entries();
            while (entries.hasMoreElements()) {
                ZipEntry entry = entries.nextElement();
                String name = entry.getName();
                if (!entry.isDirectory() && name.matches("assets/[^/]+/halo_definitions/[^/]+\\.json")) {
                    try (var reader = new InputStreamReader(zip.getInputStream(entry), StandardCharsets.UTF_8)) {
                        HaloDefinition definition = gson.fromJson(reader, HaloDefinition.class);
                        if (definition == null) throw new IllegalStateException("null definition: " + name);
                        definitions++;
                    } catch (Exception error) {
                        throw new IllegalStateException("Failed parsing " + name, error);
                    }
                }
            }
        }
        System.out.println("JAVA_PARSER_OK " + definitions);
    }
}
